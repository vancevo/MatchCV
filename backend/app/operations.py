"""Phase-6 production readiness and tenant workflow observability."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import func, select, text

from .models import (
    AgentStep,
    AgentTask,
    ApprovalRequest,
    IntegrationConnection,
    OperationalAlert,
    OperationalSLOPolicy,
    OutboxEvent,
    TenantPolicy,
    TenantUsage,
)
from .statuses import TaskStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def _check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict:
    return {"name": name, "status": "pass" if ok else ("fail" if required else "warn"), "detail": detail}


def _looks_secret(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(lowered) and "replace-with" not in lowered and "talentflow-local" not in lowered


def readiness_report(db, settings, queue: dict, *, owner_id: str | None = None) -> dict:
    """Return actionable readiness checks without exposing credentials."""
    checks: list[dict] = []
    database_ok = False
    try:
        db.execute(text("SELECT 1"))
        database_ok = True
        checks.append(_check("database", True, "Database query succeeded"))
    except Exception:
        checks.append(_check("database", False, "Database query failed"))

    checks.append(_check(
        "queue", bool(queue.get("configured") and queue.get("reachable")),
        f"Queue mode={queue.get('mode', 'unknown')}; reachable={bool(queue.get('reachable'))}",
    ))

    production_like = settings.environment in {"staging", "production"}
    if production_like:
        checks.extend([
            _check("authentication", settings.auth_required, "AUTH_REQUIRED must be true"),
            _check("seed_data", not settings.auto_seed, "AUTO_SEED must be false"),
            _check(
                "durable_queue",
                not settings.queue_eager or settings.allow_eager_real_integrations,
                "QUEUE_EAGER=false is recommended; ALLOW_EAGER_REAL_INTEGRATIONS=true permits free/demo direct dispatch",
                required=not settings.allow_eager_real_integrations,
            ),
            _check("database_engine", settings.database_url.startswith(("postgres://", "postgresql://")),
                   "PostgreSQL is required"),
            _check("public_url", settings.public_app_url.startswith("https://"), "PUBLIC_APP_URL must use HTTPS"),
        ])

    provider = settings.integration_provider
    if provider == "local":
        checks.append(_check(
            "external_integrations", not production_like,
            "Local provider has no real calendar/email side effects", required=production_like,
        ))
    else:
        if provider == "google":
            oauth_configured = bool(settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri)
        else:
            oauth_configured = bool(settings.microsoft_client_id and settings.microsoft_client_secret and settings.microsoft_redirect_uri)
        checks.extend([
            _check("oauth_configuration", oauth_configured, f"{provider.title()} OAuth credentials are configured"),
            _check("token_encryption", _looks_secret(settings.integration_token_secret),
                   "Integration token encryption secret is non-placeholder"),
            _check("oauth_state", _looks_secret(settings.oauth_state_secret),
                   "OAuth state secret is non-placeholder"),
            _check("webhook_authentication", _looks_secret(settings.provider_webhook_secret),
                   "Provider webhook secret is non-placeholder"),
        ])
        if owner_id and database_ok:
            policy = db.scalar(select(TenantPolicy).where(TenantPolicy.owner_id == owner_id))
            checks.append(_check(
                "mail_sandbox",
                bool(policy and policy.mail_sandbox_enabled and policy.mail_sandbox_base_email),
                "Mail Sandbox must be enabled before real email dispatch",
            ))
            connection = db.scalar(select(IntegrationConnection).where(
                IntegrationConnection.owner_id == owner_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.status == "ACTIVE",
            ))
            checks.append(_check("tenant_connection", bool(connection), f"Active {provider} tenant connection exists"))
        elif owner_id:
            checks.append(_check("tenant_connection", False, "Tenant connection could not be checked because database is unavailable"))

    blocking = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    return {
        "status": "ready" if not blocking else "not_ready",
        "environment": settings.environment,
        "checked_at": utcnow().isoformat(),
        "checks": checks,
        "summary": {"passed": len(checks) - len(blocking) - len(warnings), "failed": len(blocking), "warnings": len(warnings)},
    }


def _counts(db, model, owner_id: str, cutoff: datetime, statuses: list[str]) -> dict[str, int]:
    rows = db.execute(select(model.status, func.count()).where(
        model.owner_id == owner_id,
        model.created_at >= cutoff,
        model.status.in_(statuses),
    ).group_by(model.status)).all()
    values = {status: 0 for status in statuses}
    values.update({str(status): int(count) for status, count in rows})
    return values


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * percentile) - 1)]


def tenant_operations_report(db, owner_id: str, *, hours: int = 24) -> dict:
    now = utcnow()
    cutoff = now - timedelta(hours=hours)
    task_statuses = [value.value for value in TaskStatus]
    tasks = _counts(db, AgentTask, owner_id, cutoff, task_statuses)
    terminal = tasks[TaskStatus.COMPLETED.value] + tasks[TaskStatus.FAILED.value]
    success_rate = round(tasks[TaskStatus.COMPLETED.value] / terminal, 4) if terminal else None

    latencies = [int(value) for value in db.scalars(select(AgentStep.latency_ms).where(
        AgentStep.owner_id == owner_id,
        AgentStep.created_at >= cutoff,
        AgentStep.latency_ms.is_not(None),
    ))]
    outbox_statuses = ["PENDING", "PROCESSING", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED"]
    outbox = _counts(db, OutboxEvent, owner_id, cutoff, outbox_statuses)
    due_outbox = int(db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.owner_id == owner_id,
        OutboxEvent.status == "PENDING",
        OutboxEvent.available_at <= now,
    )) or 0)

    pending_approvals = int(db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.owner_id == owner_id,
        ApprovalRequest.status == "PENDING",
    )) or 0)
    stale_approvals = int(db.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.owner_id == owner_id,
        ApprovalRequest.status == "PENDING",
        ApprovalRequest.created_at <= now - timedelta(hours=24),
    )) or 0)

    period = now.strftime("%Y-%m")
    usage = db.scalar(select(TenantUsage).where(TenantUsage.owner_id == owner_id, TenantUsage.period == period))
    policy = db.scalar(select(TenantPolicy).where(TenantPolicy.owner_id == owner_id))
    usage_payload = {
        "period": period,
        "screenings": usage.screenings if usage else 0,
        "tokens": (usage.input_tokens + usage.output_tokens) if usage else 0,
        "cost_micros": usage.cost_micros if usage else 0,
        "limits": {
            "screenings": policy.monthly_screening_limit if policy else 10000,
            "tokens": policy.monthly_token_limit if policy else 10000000,
            "cost_micros": policy.monthly_cost_limit_micros if policy else 100000000,
        },
    }
    degraded = tasks[TaskStatus.FAILED.value] > 0 or outbox["FAILED"] > 0 or outbox["BLOCKED"] > 0 or due_outbox > 0
    return {
        "status": "degraded" if degraded else "healthy",
        "window": {"hours": hours, "from": cutoff.isoformat(), "to": now.isoformat()},
        "screening": {"tasks": tasks, "terminal_success_rate": success_rate,
                      "step_latency_ms": {"samples": len(latencies), "p50": _percentile(latencies, .5),
                                          "p95": _percentile(latencies, .95)}},
        "outbox": {"events": outbox, "due_backlog": due_outbox},
        "approvals": {"pending": pending_approvals, "older_than_24h": stale_approvals},
        "usage": usage_payload,
    }


def slo_policy_for(db, owner_id: str) -> OperationalSLOPolicy:
    policy = db.scalar(select(OperationalSLOPolicy).where(OperationalSLOPolicy.owner_id == owner_id))
    if policy:
        return policy
    policy = OperationalSLOPolicy(owner_id=owner_id)
    db.add(policy)
    db.flush()
    return policy


def slo_policy_dict(policy: OperationalSLOPolicy) -> dict:
    return {
        "window_hours": policy.window_hours,
        "min_screening_success_rate": policy.min_screening_success_rate,
        "max_p95_latency_ms": policy.max_p95_latency_ms,
        "max_due_outbox": policy.max_due_outbox,
        "max_failed_outbox": policy.max_failed_outbox,
        "max_stale_approvals": policy.max_stale_approvals,
        "budget_warning_percent": policy.budget_warning_percent,
        "min_canary_samples": policy.min_canary_samples,
        "max_success_rate_drop": policy.max_success_rate_drop,
        "max_latency_regression_percent": policy.max_latency_regression_percent,
        "max_cost_regression_percent": policy.max_cost_regression_percent,
        "notifications_enabled": policy.notifications_enabled,
        "notification_email": policy.notification_email,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


def alert_dict(value: OperationalAlert) -> dict:
    return {
        "id": value.id, "signal": value.signal, "severity": value.severity, "status": value.status,
        "summary": value.summary, "observed": value.observed, "threshold": value.threshold,
        "occurrences": value.occurrences,
        "opened_at": utc_iso(value.opened_at), "last_observed_at": utc_iso(value.last_observed_at),
        "acknowledged_at": utc_iso(value.acknowledged_at) if value.acknowledged_at else None,
        "resolved_at": utc_iso(value.resolved_at) if value.resolved_at else None,
    }


def _budget_percent(used: int, limit: int) -> float:
    return round((used / limit) * 100, 2) if limit > 0 else 100.0


def evaluate_slo_alerts(db, owner_id: str) -> tuple[dict, list[OperationalAlert]]:
    """Upsert one rolling alert per signal and auto-resolve recovered signals."""
    policy = slo_policy_for(db, owner_id)
    report = tenant_operations_report(db, owner_id, hours=policy.window_hours)
    screening = report["screening"]
    outbox = report["outbox"]
    approvals = report["approvals"]
    usage = report["usage"]
    breaches: dict[str, tuple[str, str, dict, dict]] = {}

    success_rate = screening["terminal_success_rate"]
    if success_rate is not None and success_rate < policy.min_screening_success_rate:
        breaches["screening_success_rate"] = (
            "CRITICAL", "Screening success rate is below SLO",
            {"value": success_rate}, {"minimum": policy.min_screening_success_rate},
        )
    p95 = screening["step_latency_ms"]["p95"]
    if p95 is not None and p95 > policy.max_p95_latency_ms:
        breaches["screening_p95_latency"] = (
            "WARNING", "Screening P95 step latency exceeds SLO",
            {"value_ms": p95}, {"maximum_ms": policy.max_p95_latency_ms},
        )
    if outbox["due_backlog"] > policy.max_due_outbox:
        breaches["outbox_due_backlog"] = (
            "CRITICAL", "Outbox has due events waiting for dispatch",
            {"value": outbox["due_backlog"]}, {"maximum": policy.max_due_outbox},
        )
    failed_outbox = outbox["events"]["FAILED"] + outbox["events"]["BLOCKED"]
    if failed_outbox > policy.max_failed_outbox:
        breaches["outbox_failures"] = (
            "CRITICAL", "Outbox failures or policy blocks exceed SLO",
            {"value": failed_outbox}, {"maximum": policy.max_failed_outbox},
        )
    if approvals["older_than_24h"] > policy.max_stale_approvals:
        breaches["stale_approvals"] = (
            "WARNING", "Approval requests have exceeded the 24-hour SLA",
            {"value": approvals["older_than_24h"]}, {"maximum": policy.max_stale_approvals},
        )
    for key in ("screenings", "tokens", "cost_micros"):
        percent = _budget_percent(usage[key], usage["limits"][key])
        if percent >= policy.budget_warning_percent:
            breaches[f"budget_{key}"] = (
                "WARNING", f"Tenant {key} budget is near its limit",
                {"percent": percent, "used": usage[key]},
                {"warning_percent": policy.budget_warning_percent, "limit": usage["limits"][key]},
            )

    now = utcnow()
    existing = {value.signal: value for value in db.scalars(select(OperationalAlert).where(
        OperationalAlert.owner_id == owner_id
    ))}
    for signal, (severity, summary, observed, threshold) in breaches.items():
        alert = existing.get(signal)
        if not alert:
            alert = OperationalAlert(owner_id=owner_id, signal=signal, severity=severity, status="OPEN",
                                     summary=summary, observed=observed, threshold=threshold)
            db.add(alert)
            existing[signal] = alert
        else:
            if alert.status == "RESOLVED":
                alert.opened_at = now
                alert.acknowledged_at = None
                alert.resolved_at = None
            alert.status = "OPEN"
            alert.severity = severity
            alert.summary = summary
            alert.observed = observed
            alert.threshold = threshold
            alert.occurrences += 1
            alert.last_observed_at = now
    for signal, alert in existing.items():
        if signal not in breaches and alert.status in {"OPEN", "ACKNOWLEDGED"}:
            alert.status = "RESOLVED"
            alert.resolved_at = now
    db.flush()
    return report, sorted(existing.values(), key=lambda value: value.last_observed_at, reverse=True)


def evaluate_canary(baseline: dict, candidate: dict, policy: OperationalSLOPolicy) -> dict:
    """Apply deterministic promotion rules to aggregate canary telemetry."""
    reasons: list[str] = []
    samples = int(candidate.get("samples", 0))
    if samples < policy.min_canary_samples:
        reasons.append(f"candidate samples {samples} < required {policy.min_canary_samples}")

    base_success = float(baseline.get("success_rate", 0))
    candidate_success = float(candidate.get("success_rate", 0))
    if candidate_success < policy.min_screening_success_rate:
        reasons.append("candidate success rate is below the absolute SLO")
    if base_success - candidate_success > policy.max_success_rate_drop:
        reasons.append("candidate success rate regression exceeds the allowed drop")

    base_latency = int(baseline.get("p95_latency_ms", 0))
    candidate_latency = int(candidate.get("p95_latency_ms", 0))
    if candidate_latency > policy.max_p95_latency_ms:
        reasons.append("candidate P95 latency exceeds the absolute SLO")
    if base_latency > 0 and candidate_latency > base_latency * (1 + policy.max_latency_regression_percent / 100):
        reasons.append("candidate P95 latency regression exceeds the allowed percentage")

    base_cost = int(baseline.get("cost_per_screening_micros", 0))
    candidate_cost = int(candidate.get("cost_per_screening_micros", 0))
    if base_cost > 0 and candidate_cost > base_cost * (1 + policy.max_cost_regression_percent / 100):
        reasons.append("candidate cost regression exceeds the allowed percentage")
    if int(candidate.get("outbox_failures", 0)) > 0:
        reasons.append("candidate produced outbox failures")
    return {"status": "PROMOTION_ALLOWED" if not reasons else "PROMOTION_BLOCKED", "reasons": reasons}


def run_operational_sweep(owner_id: str | None = None, *, schedule_next: bool = True) -> dict:
    """Evaluate configured tenants, enqueue one notification per alert episode, then schedule the next sweep."""
    from .database import session_scope
    from .scheduling import add_outbox, dispatch_outbox

    notification_ids: list[str] = []
    evaluated = active_alerts = 0
    with session_scope() as db:
        statement = select(OperationalSLOPolicy.owner_id)
        if owner_id:
            statement = statement.where(OperationalSLOPolicy.owner_id == owner_id)
        owner_ids = list(db.scalars(statement))
        for tenant_id in owner_ids:
            _report, alerts = evaluate_slo_alerts(db, tenant_id)
            policy = slo_policy_for(db, tenant_id)
            active = [value for value in alerts if value.status in {"OPEN", "ACKNOWLEDGED"}]
            evaluated += 1
            active_alerts += len(active)
            if not policy.notifications_enabled or not policy.notification_email:
                continue
            for alert in active:
                episode = utc_iso(alert.opened_at)
                event = add_outbox(
                    db,
                    owner_id=tenant_id,
                    aggregate_type="operational_alert",
                    aggregate_id=alert.id,
                    operation="EMAIL_SEND",
                    payload={
                        "to": policy.notification_email,
                        "subject": f"[{alert.severity}] TalentFlow: {alert.signal}",
                        "body": f"{alert.summary}\nObserved: {alert.observed}\nThreshold: {alert.threshold}",
                        "kind": "OPERATIONAL_ALERT",
                    },
                    idempotency_key=f"operational-alert:{alert.id}:{episode}",
                )
                if event.status == "PENDING":
                    notification_ids.append(event.id)
    for event_id in dict.fromkeys(notification_ids):
        dispatch_outbox(event_id)
    if schedule_next:
        schedule_operational_sweep()
    return {"tenants_evaluated": evaluated, "active_alerts": active_alerts,
            "notifications_dispatched": len(set(notification_ids))}


def schedule_operational_sweep(*, initial: bool = False) -> bool:
    """Schedule the global evaluator on the existing durable RQ scheduler."""
    from .config import get_settings

    settings = get_settings()
    if settings.queue_eager:
        return False
    from redis import Redis
    from rq import Queue

    delay = timedelta(seconds=5) if initial else timedelta(minutes=settings.operational_sweep_interval_minutes)
    run_at = utcnow() + delay
    job_id = f"operational-slo-sweep:{run_at.strftime('%Y%m%d%H%M')}"
    try:
        queue = Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url))
        if queue.fetch_job(job_id):
            return False
        queue.enqueue_at(run_at, run_operational_sweep, job_id=job_id, retry=None, result_ttl=86400)
        return True
    except Exception:
        # Queue readiness exposes the outage; API startup must remain diagnosable.
        return False
