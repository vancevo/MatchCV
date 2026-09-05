"""Phase-5 governance primitives kept independent from HTTP handlers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from .models import ModelPolicy, TenantPolicy, TenantRateWindow, TenantUsage


EVAL_THRESHOLDS = {
    "requirement_accuracy": 1.0,
    "evidence_support_rate": 1.0,
    "top_1_ranking_accuracy": 1.0,
    "manual_review_routing_accuracy": 1.0,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def policy_for(db, owner_id: str) -> TenantPolicy:
    value = db.scalar(select(TenantPolicy).where(TenantPolicy.owner_id == owner_id))
    if value:
        return value
    value = TenantPolicy(owner_id=owner_id)
    db.add(value)
    db.flush()
    return value


def usage_for(db, owner_id: str) -> TenantUsage:
    period = utcnow().strftime("%Y-%m")
    value = db.scalar(select(TenantUsage).where(TenantUsage.owner_id == owner_id, TenantUsage.period == period))
    if value:
        return value
    value = TenantUsage(owner_id=owner_id, period=period)
    db.add(value)
    db.flush()
    return value


def enforce_screening_budget(db, owner_id: str) -> None:
    policy = policy_for(db, owner_id)
    usage = usage_for(db, owner_id)
    if usage.screenings >= policy.monthly_screening_limit:
        raise HTTPException(429, "Tenant monthly screening limit reached")
    if usage.input_tokens + usage.output_tokens >= policy.monthly_token_limit:
        raise HTTPException(429, "Tenant monthly token limit reached")
    if usage.cost_micros >= policy.monthly_cost_limit_micros:
        raise HTTPException(429, "Tenant monthly AI budget reached")
    window_key = utcnow().strftime("%Y%m%d%H%M")
    window = db.scalar(select(TenantRateWindow).where(
        TenantRateWindow.owner_id == owner_id, TenantRateWindow.window_key == window_key
    ))
    if not window:
        window = TenantRateWindow(owner_id=owner_id, window_key=window_key)
        db.add(window); db.flush()
    if window.request_count >= policy.screenings_per_minute:
        raise HTTPException(429, "Tenant screening rate limit reached")
    window.request_count += 1
    window.updated_at = utcnow()


def record_screening_usage(db, owner_id: str, input_tokens: int, output_tokens: int, cost_micros: int) -> TenantUsage:
    usage = usage_for(db, owner_id)
    usage.screenings += 1
    usage.input_tokens += max(0, input_tokens)
    usage.output_tokens += max(0, output_tokens)
    usage.cost_micros += max(0, cost_micros)
    usage.updated_at = utcnow()
    return usage


def evaluation_passes(metrics: dict) -> bool:
    return all(float(metrics.get(key, 0)) >= minimum for key, minimum in EVAL_THRESHOLDS.items())


def select_model_variant(db, owner_id: str, stable_key: str) -> tuple[str, dict]:
    policy = db.scalar(select(ModelPolicy).where(
        ModelPolicy.owner_id == owner_id,
        ModelPolicy.workflow == "candidate_screening",
        ModelPolicy.status == "ACTIVE",
    ))
    if not policy:
        return "champion", {}
    bucket = int(hashlib.sha256(stable_key.encode()).hexdigest()[:8], 16) % 100
    if policy.challenger_percent and bucket < policy.challenger_percent:
        return "challenger", dict(policy.challenger or {})
    return "champion", dict(policy.champion or {})


def retention_cutoff(policy: TenantPolicy) -> datetime:
    return utcnow() - timedelta(days=policy.retention_days)
