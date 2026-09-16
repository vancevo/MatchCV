from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import jwt
from cryptography.fernet import Fernet
from sqlalchemy import select

from .config import get_settings
from .database import session_scope
from .models import (
    Application,
    AuditLog,
    IntegrationConnection,
    Interview,
    Job,
    OutboxEvent,
    EmailTemplate,
    SchedulingInvitation,
    TenantPolicy,
)


DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
VN_WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def local_datetime_label(value: datetime, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    """A candidate reads '14:00 Thứ Tư, 17/09/2026', not '2026-09-17T07:00:00+00:00'."""
    try:
        zone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    local = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(zone)
    return f"{local:%H:%M} {VN_WEEKDAYS[local.weekday()]}, {local:%d/%m/%Y}"


# Bump this whenever the wording below changes. render_template then replaces any tenant row that
# still carries a superseded built-in body, while leaving copy someone wrote through
# POST /api/email-templates alone.
BUILTIN_TEMPLATE_VERSION = 2

BUILTIN_TEMPLATES: dict[str, dict[str, str]] = {
    "scheduling_invitation": {
        "subject": "Thư mời phỏng vấn vị trí {job_title}",
        "body": (
            "Kính gửi {candidate_name},\n\n"
            "Cảm ơn bạn đã quan tâm và ứng tuyển vị trí {job_title}.\n\n"
            "Sau khi xem xét hồ sơ, chúng tôi trân trọng mời bạn tham gia buổi phỏng vấn "
            "để hai bên có cơ hội trao đổi kỹ hơn về công việc cũng như định hướng của bạn.\n\n"
            "THÔNG TIN BUỔI PHỎNG VẤN\n"
            "- Vị trí:    {job_title}\n"
            "- Hình thức: Phỏng vấn trực tuyến\n"
            "- Thời lượng: {duration_minutes} phút\n\n"
            "Bạn vui lòng chọn khung giờ thuận tiện nhất tại đường dẫn sau:\n"
            "{public_url}\n\n"
            "Đường dẫn có hiệu lực đến {expires_at}. Khung giờ bạn chọn sẽ được giữ riêng, "
            "và chúng tôi sẽ gửi email xác nhận kèm đường dẫn phòng họp ngay sau đó.\n\n"
            "Nếu không có khung giờ nào phù hợp hoặc bạn cần hỗ trợ thêm, "
            "bạn chỉ cần phản hồi lại email này.\n\n"
            "Trân trọng,\n"
            "Bộ phận Tuyển dụng"
        ),
    },
    "interview_confirmation": {
        "subject": "Xác nhận lịch phỏng vấn vị trí {job_title}",
        "body": (
            "Kính gửi {candidate_name},\n\n"
            "Lịch phỏng vấn của bạn đã được xác nhận. Bạn vui lòng lưu lại thông tin dưới đây:\n\n"
            "- Vị trí:    {job_title}\n"
            "- Thời gian: {start_at}\n"
            "- Hình thức: Phỏng vấn trực tuyến\n"
            "- Phòng họp: {meeting_url}\n\n"
            "Bạn nên vào phòng họp trước giờ hẹn khoảng 5 phút và kiểm tra trước đường truyền, "
            "micro cùng camera để buổi trao đổi diễn ra thuận lợi.\n\n"
            "Trường hợp cần dời sang khung giờ khác, bạn có thể tự chọn lại tại đây:\n"
            "{reschedule_url}\n\n"
            "Chúng tôi rất mong được trò chuyện cùng bạn.\n\n"
            "Trân trọng,\n"
            "Bộ phận Tuyển dụng"
        ),
    },
    "interview_reminder": {
        "subject": "Nhắc lịch phỏng vấn vị trí {job_title}",
        "body": (
            "Kính gửi {candidate_name},\n\n"
            "Chúng tôi xin nhắc bạn về buổi phỏng vấn sắp diễn ra:\n\n"
            "- Vị trí:    {job_title}\n"
            "- Thời gian: {start_at}\n"
            "- Phòng họp: {meeting_url}\n\n"
            "Bạn vui lòng tham gia đúng giờ. Nếu có việc đột xuất khiến bạn không thể tham dự, "
            "rất mong bạn phản hồi sớm để chúng tôi kịp sắp xếp lại lịch.\n\n"
            "Trân trọng,\n"
            "Bộ phận Tuyển dụng"
        ),
    },
    "scorecard_reminder": {
        "subject": "Nhắc nộp phiếu đánh giá ứng viên {candidate_name}",
        "body": (
            "Xin chào,\n\n"
            "Buổi phỏng vấn dưới đây đã kết thúc nhưng phiếu đánh giá chưa được nộp:\n\n"
            "- Ứng viên: {candidate_name}\n"
            "- Vị trí:   {job_title}\n"
            "- Hạn nộp:  {deadline}\n\n"
            "Bạn vui lòng hoàn tất phiếu đánh giá kèm dẫn chứng cụ thể trước hạn, "
            "để nhóm tuyển dụng có đủ cơ sở đưa ra quyết định.\n\n"
            "Trân trọng,\n"
            "TalentFlow"
        ),
    },
}

# Version 1 copy. A row still holding one of these was auto-seeded, never hand-written, so it is
# safe to supersede; anything else is treated as a deliberate customisation.
SUPERSEDED_BUILTIN_BODIES: dict[str, set[str]] = {
    "scheduling_invitation": {
        "Chào {candidate_name},\n\nBạn đã vào danh sách phỏng vấn. Vui lòng chọn lịch tại: {public_url}\n"
        "Liên kết hết hạn lúc {expires_at}. Lịch bạn chọn sẽ được giữ riêng và chờ HR xác nhận.",
    },
    "interview_confirmation": {
        "Chào {candidate_name},\n\nLịch phỏng vấn của bạn: {start_at} UTC.\n"
        "Tham gia: {meeting_url}\nĐổi lịch: {reschedule_url}",
    },
    "interview_reminder": {
        "Chào {candidate_name},\n\nLịch phỏng vấn bắt đầu lúc {start_at}.\nTham gia: {meeting_url}",
    },
    "scorecard_reminder": {
        "Scorecard cho {candidate_name} / {job_title} chưa được nộp. "
        "Vui lòng bổ sung evidence trước {deadline}.",
    },
}


GOOGLE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
)
MICROSOFT_SCOPES = ("openid", "email", "offline_access", "User.Read", "Calendars.ReadWrite", "Mail.Send")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mail_sandbox_alias(base_email: str, alias_number: int) -> str:
    local, separator, domain = base_email.strip().lower().partition("@")
    if not separator or not local or not domain or "+" in local or alias_number < 1:
        raise ValueError("Invalid sandbox base email or alias number")
    return f"{local}+{alias_number}@{domain}"


def mail_sandbox_recipient_allowed(policy: TenantPolicy | None, recipient: str) -> bool:
    """Validate without rewriting so the plus alias remains in stored records and provider payloads."""
    if not policy or not policy.mail_sandbox_enabled:
        return True
    base = policy.mail_sandbox_base_email.strip().lower()
    candidate = recipient.strip().lower()
    if not base or candidate == base:
        return candidate == base
    local, separator, domain = base.partition("@")
    match = re.fullmatch(rf"{re.escape(local)}\+(\d+)@{re.escape(domain)}", candidate)
    return bool(match and 1 <= int(match.group(1)) <= policy.mail_sandbox_max_alias)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=value.tzinfo or timezone.utc) if value else None


class TokenCipher:
    def __init__(self, secret: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self.fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode()).decode() if value else ""


def oauth_state(owner_id: str, provider: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {"sub": owner_id, "provider": provider, "exp": utcnow() + timedelta(minutes=10), "jti": str(uuid4())},
        settings.oauth_state_secret,
        algorithm="HS256",
    )


def decode_oauth_state(state: str, provider: str) -> str:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.oauth_state_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired OAuth state") from exc
    if payload.get("provider") != provider or not payload.get("sub"):
        raise ValueError("OAuth state provider mismatch")
    return str(payload["sub"])


def authorization_url(provider: str, owner_id: str) -> str:
    settings = get_settings()
    state = oauth_state(owner_id, provider)
    if provider == "google":
        if not settings.google_client_id or not settings.google_client_secret:
            raise ValueError("Google OAuth is not configured")
        query = urlencode({
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        })
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    if provider == "microsoft":
        if not settings.microsoft_client_id or not settings.microsoft_client_secret:
            raise ValueError("Microsoft OAuth is not configured")
        query = urlencode({
            "client_id": settings.microsoft_client_id,
            "redirect_uri": settings.microsoft_redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state,
        })
        return f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{query}"
    raise ValueError("Unsupported provider")


def _post_form(url: str, data: dict[str, str]) -> dict:
    response = httpx.post(url, data=data, timeout=20)
    response.raise_for_status()
    return response.json()


def exchange_code(provider: str, code: str) -> dict:
    settings = get_settings()
    if provider == "google":
        return _post_form("https://oauth2.googleapis.com/token", {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
        })
    if provider == "microsoft":
        return _post_form("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "redirect_uri": settings.microsoft_redirect_uri,
            "grant_type": "authorization_code",
            "scope": " ".join(MICROSOFT_SCOPES),
            "code": code,
        })
    raise ValueError("Unsupported provider")


def save_connection(owner_id: str, provider: str, tokens: dict) -> IntegrationConnection:
    settings = get_settings()
    cipher = TokenCipher(settings.integration_token_secret)
    expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    scopes = str(tokens.get("scope", "")).split()
    account_email = ""
    try:
        if provider == "google":
            profile = _request("GET", "https://openidconnect.googleapis.com/v1/userinfo", str(tokens["access_token"]))
            account_email = str(profile.get("email", ""))
        else:
            profile = _request("GET", "https://graph.microsoft.com/v1.0/me", str(tokens["access_token"]))
            account_email = str(profile.get("mail") or profile.get("userPrincipalName") or "")
    except httpx.HTTPError:
        pass
    with session_scope() as db:
        value = db.scalar(select(IntegrationConnection).where(
            IntegrationConnection.owner_id == owner_id,
            IntegrationConnection.provider == provider,
        ))
        if not value:
            value = IntegrationConnection(
                owner_id=owner_id, provider=provider, access_token_encrypted="", refresh_token_encrypted=""
            )
            db.add(value)
        value.status = "ACTIVE"
        value.account_email = account_email or value.account_email
        value.revoked_at = None
        value.access_token_encrypted = cipher.encrypt(str(tokens["access_token"]))
        if tokens.get("refresh_token"):
            value.refresh_token_encrypted = cipher.encrypt(str(tokens["refresh_token"]))
        value.expires_at = expires_at
        value.scopes = scopes
        db.flush()
        db.refresh(value)
        return value


def _refresh(connection: IntegrationConnection) -> str:
    settings = get_settings()
    cipher = TokenCipher(settings.integration_token_secret)
    if _aware(connection.expires_at) and _aware(connection.expires_at) > utcnow() + timedelta(minutes=2):
        return cipher.decrypt(connection.access_token_encrypted)
    refresh_token = cipher.decrypt(connection.refresh_token_encrypted)
    if not refresh_token:
        raise RuntimeError("Provider connection has no refresh token")
    if connection.provider == "google":
        tokens = _post_form("https://oauth2.googleapis.com/token", {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
    else:
        tokens = _post_form("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "grant_type": "refresh_token",
            "scope": " ".join(MICROSOFT_SCOPES),
            "refresh_token": refresh_token,
        })
    connection.access_token_encrypted = cipher.encrypt(str(tokens["access_token"]))
    if tokens.get("refresh_token"):
        connection.refresh_token_encrypted = cipher.encrypt(str(tokens["refresh_token"]))
    connection.expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    return str(tokens["access_token"])


def _request(method: str, url: str, token: str, *, json_body: dict | None = None, headers: dict | None = None) -> Any:
    request_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **(headers or {})}
    response = httpx.request(method, url, headers=request_headers, json=json_body, timeout=20)
    response.raise_for_status()
    return response.json() if response.content else {}


def _connection(db, owner_id: str) -> IntegrationConnection | None:
    provider = get_settings().integration_provider
    if provider == "local":
        return None
    value = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.owner_id == owner_id,
        IntegrationConnection.provider == provider,
        IntegrationConnection.status == "ACTIVE",
    ))
    if not value:
        raise RuntimeError(f"No active {provider} connection")
    return value


def provider_busy(db, owner_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    connection = _connection(db, owner_id)
    if not connection:
        return []
    token = _refresh(connection)
    if connection.provider == "google":
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "timeZone": "UTC", "items": [{"id": "primary"}]}
        result = _request("POST", "https://www.googleapis.com/calendar/v3/freeBusy", token, json_body=body)
        ranges = result.get("calendars", {}).get("primary", {}).get("busy", [])
        return [(datetime.fromisoformat(x["start"].replace("Z", "+00:00")), datetime.fromisoformat(x["end"].replace("Z", "+00:00"))) for x in ranges]
    body = {
        "schedules": [connection.account_email or "me"],
        "startTime": {"dateTime": start.replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
        "endTime": {"dateTime": end.replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
        "availabilityViewInterval": 30,
    }
    result = _request("POST", "https://graph.microsoft.com/v1.0/me/calendar/getSchedule", token, json_body=body)
    items = (result.get("value") or [{}])[0].get("scheduleItems", [])
    return [(datetime.fromisoformat(x["start"]["dateTime"]).replace(tzinfo=timezone.utc), datetime.fromisoformat(x["end"]["dateTime"]).replace(tzinfo=timezone.utc)) for x in items]


def available_slots_for_owner(db, owner_id: str, duration_minutes: int = 60, days: int = 7) -> list[datetime]:
    start = utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    end = start + timedelta(days=days)
    try:
        busy = provider_busy(db, owner_id, start, end)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as error:
        # The calendar provider being unreachable used to raise straight out of the public
        # scheduling route: the candidate got an opaque 500 and could not book at all. Falling
        # back to the interviews we already hold keeps the invitation usable; the audit row is
        # what tells the recruiter the free/busy read was skipped.
        busy = []
        db.add(AuditLog(owner_id=owner_id, application_id=None, action="PROVIDER_FREEBUSY_UNAVAILABLE",
                        metadata_json={"error": str(error)[:500]}))
    busy.extend(((_aware(i.start_at) or start), (_aware(i.end_at) or end)) for i in db.scalars(
        select(Interview).where(Interview.owner_id == owner_id, Interview.status != "CANCELLED", Interview.start_at < end, Interview.end_at > start)
    ))
    slots: list[datetime] = []
    cursor = start
    duration = timedelta(minutes=duration_minutes)
    while cursor + duration <= end and len(slots) < 20:
        local = cursor.astimezone(timezone(timedelta(hours=7)))
        if local.weekday() < 5 and 9 <= local.hour < 17 and not any(cursor < b_end and cursor + duration > b_start for b_start, b_end in busy):
            slots.append(cursor)
        cursor += timedelta(hours=1)
    return slots


def add_outbox(db, *, owner_id: str, aggregate_type: str, aggregate_id: str, operation: str,
               payload: dict, idempotency_key: str) -> OutboxEvent:
    existing = db.scalar(select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key))
    if existing:
        return existing
    value = OutboxEvent(owner_id=owner_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                        operation=operation, payload=payload, idempotency_key=idempotency_key)
    db.add(value)
    db.flush()
    return value


def render_template(db, owner_id: str, key: str, context: dict[str, str]) -> dict:
    builtin = BUILTIN_TEMPLATES[key]
    template = db.scalar(select(EmailTemplate).where(
        EmailTemplate.owner_id == owner_id, EmailTemplate.key == key, EmailTemplate.active.is_(True),
    ).order_by(EmailTemplate.version.desc()))
    if (template and template.version < BUILTIN_TEMPLATE_VERSION
            and (template.body_text or "").strip() in SUPERSEDED_BUILTIN_BODIES.get(key, set())):
        template.active = False
        template = None
    if not template:
        template = EmailTemplate(owner_id=owner_id, key=key, version=BUILTIN_TEMPLATE_VERSION,
                                 subject=builtin["subject"], body_text=builtin["body"], active=True)
        db.add(template); db.flush()
    # A template written before a new field existed must not crash the send.
    safe = _TemplateContext(context)
    return {"template_id": template.id, "template_key": key, "template_version": template.version,
            "subject": template.subject.format_map(safe), "body": template.body_text.format_map(safe)}


class _TemplateContext(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _create_event(connection: IntegrationConnection | None, token: str, interview: Interview,
                  application: Application, job: Job, idempotency_key: str) -> tuple[str, str]:
    if not connection:
        return f"local-{interview.id}", f"https://meet.example/{interview.id[:8]}"
    start = (_aware(interview.start_at) or utcnow()).isoformat()
    end = (_aware(interview.end_at) or utcnow()).isoformat()
    if connection.provider == "google":
        event_id = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        body = {
            "id": event_id,
            "summary": f"Phỏng vấn {job.title} — {application.candidate_name}",
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "attendees": [{"email": application.candidate_email}] if application.candidate_email else [],
            "conferenceData": {"createRequest": {"requestId": idempotency_key[:64], "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        }
        try:
            result = _request("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1&sendUpdates=none", token, json_body=body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            result = _request("GET", f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}", token)
        return str(result["id"]), str(result.get("hangoutLink") or result.get("htmlLink") or "")
    body = {
        "subject": f"Phỏng vấn {job.title} — {application.candidate_name}",
        "start": {"dateTime": start, "timeZone": "UTC"}, "end": {"dateTime": end, "timeZone": "UTC"},
        "attendees": ([{"emailAddress": {"address": application.candidate_email, "name": application.candidate_name}, "type": "required"}] if application.candidate_email else []),
        "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness",
        "transactionId": idempotency_key,
    }
    result = _request("POST", "https://graph.microsoft.com/v1.0/me/events", token, json_body=body)
    return str(result["id"]), str((result.get("onlineMeeting") or {}).get("joinUrl") or result.get("webLink") or "")


def _send_email(connection: IntegrationConnection | None, token: str, payload: dict) -> str:
    if not payload.get("to"):
        return "skipped-no-recipient"
    if not connection:
        return f"local-mail-{uuid4()}"
    if connection.provider == "google":
        message = EmailMessage()
        message["To"] = payload["to"]
        message["Subject"] = payload["subject"]
        message["Message-ID"] = f"<{hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}@talentflow.local>"
        message.set_content(payload["body"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        result = _request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", token, json_body={"raw": raw})
        return str(result["id"])
    body = {"message": {"subject": payload["subject"], "body": {"contentType": "Text", "content": payload["body"]},
                        "toRecipients": [{"emailAddress": {"address": payload["to"]}}]}, "saveToSentItems": True}
    _request("POST", "https://graph.microsoft.com/v1.0/me/sendMail", token, json_body=body)
    return f"microsoft-mail-{uuid4()}"


def _update_event(connection: IntegrationConnection | None, token: str, interview: Interview) -> str:
    if not connection:
        return interview.external_event_id or f"local-{interview.id}"
    start = (_aware(interview.start_at) or utcnow()).isoformat()
    end = (_aware(interview.end_at) or utcnow()).isoformat()
    if connection.provider == "google":
        _request("PATCH", f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{interview.external_event_id}",
                 token, json_body={"start": {"dateTime": start}, "end": {"dateTime": end}})
    else:
        _request("PATCH", f"https://graph.microsoft.com/v1.0/me/events/{interview.external_event_id}", token,
                 json_body={"start": {"dateTime": start, "timeZone": "UTC"}, "end": {"dateTime": end, "timeZone": "UTC"}})
    return interview.external_event_id or ""


def _cancel_event(connection: IntegrationConnection | None, token: str, interview: Interview) -> str:
    if connection and interview.external_event_id:
        url = (f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{interview.external_event_id}"
               if connection.provider == "google" else f"https://graph.microsoft.com/v1.0/me/events/{interview.external_event_id}")
        _request("DELETE", url, token)
    interview.status = "CANCELLED"
    return interview.external_event_id or f"local-{interview.id}"


def process_outbox_event(event_id: str) -> None:
    child_event_ids: list[str] = []
    follow_up_sweeps: list[tuple[str, datetime, str]] = []
    retry_delay: int | None = None
    with session_scope() as db:
        event = db.get(OutboxEvent, event_id)
        if not event or event.status == "COMPLETED" or _aware(event.available_at) > utcnow():
            return
        policy = db.scalar(select(TenantPolicy).where(TenantPolicy.owner_id == event.owner_id))
        blocked = bool(policy and (
            (event.operation == "EMAIL_SEND" and not policy.email_enabled)
            or (event.operation.startswith("CALENDAR_") and not policy.calendar_enabled)
        ))
        if blocked:
            event.status = "BLOCKED"
            event.last_error = "Blocked by tenant kill switch"
            db.add(AuditLog(owner_id=event.owner_id, application_id=None, action="OUTBOX_POLICY_BLOCKED",
                            metadata_json={"outbox_id": event.id, "operation": event.operation}))
            return
        sandbox_recipient = str((event.payload or {}).get("to", "")) if event.operation == "EMAIL_SEND" else ""
        if event.operation == "CALENDAR_CREATE":
            interview = db.get(Interview, event.aggregate_id)
            application = db.get(Application, interview.application_id) if interview else None
            sandbox_recipient = application.candidate_email if application else ""
        settings = get_settings()
        if event.operation == "EMAIL_SEND" and settings.integration_provider != "local" and (
            not policy or not policy.mail_sandbox_enabled or not policy.mail_sandbox_base_email
        ):
            event.status = "BLOCKED"
            event.last_error = "Mail Sandbox must be enabled before real email dispatch"
            db.add(AuditLog(owner_id=event.owner_id, application_id=None, action="MAIL_SANDBOX_REQUIRED",
                            metadata_json={"outbox_id": event.id, "operation": event.operation}))
            return
        if sandbox_recipient and not mail_sandbox_recipient_allowed(policy, sandbox_recipient):
            event.status = "BLOCKED"
            event.last_error = "Recipient is outside the tenant mail sandbox whitelist"
            db.add(AuditLog(owner_id=event.owner_id, application_id=None, action="MAIL_SANDBOX_RECIPIENT_BLOCKED",
                            metadata_json={"outbox_id": event.id, "recipient": sandbox_recipient,
                                           "operation": event.operation}))
            return
        event.status = "PROCESSING"
        event.attempts += 1
        try:
            connection = _connection(db, event.owner_id)
            token = _refresh(connection) if connection else ""
            if event.operation == "CALENDAR_CREATE":
                interview = db.get(Interview, event.aggregate_id)
                if not interview:
                    raise RuntimeError("Interview no longer exists")
                application = db.get(Application, interview.application_id)
                job = db.get(Job, application.job_id) if application else None
                if not application or not job:
                    raise RuntimeError("Interview application/job no longer exists")
                external_id, meeting_url = _create_event(connection, token, interview, application, job, event.idempotency_key)
                interview.external_event_id = external_id
                interview.meeting_url = meeting_url
                interview.provider = connection.provider if connection else "local"
                interview.status = "SCHEDULED"
                application.status = "INTERVIEW_SCHEDULED"
                event.provider_message_id = external_id
                reschedule_token = secrets.token_urlsafe(32)
                reschedule_invitation = SchedulingInvitation(
                    owner_id=event.owner_id, application_id=application.id,
                    token_hash=hashlib.sha256(reschedule_token.encode()).hexdigest(),
                    purpose="RESCHEDULE", timezone_name=interview.timezone_name,
                    duration_minutes=max(30, int((interview.end_at - interview.start_at).total_seconds() // 60)),
                    expires_at=(_aware(interview.start_at) or utcnow()) + timedelta(days=7),
                )
                db.add(reschedule_invitation)
                rendered = render_template(
                    db, event.owner_id, "interview_confirmation",
                    {"job_title": job.title, "candidate_name": application.candidate_name,
                     "start_at": local_datetime_label(interview.start_at, interview.timezone_name),
                     "meeting_url": meeting_url or "(sẽ gửi trước buổi phỏng vấn)",
                     "reschedule_url": f"{get_settings().public_app_url}/?schedule={reschedule_token}"},
                )
                child = add_outbox(db, owner_id=event.owner_id, aggregate_type="interview", aggregate_id=interview.id,
                                   operation="EMAIL_SEND", idempotency_key=f"interview-confirmation:{interview.id}", payload={
                               "to": application.candidate_email,
                               **rendered,
                           })
                child_event_ids.append(child.id)
                from .interview_ops import get_policy, schedule_reminders, schedule_scorecard_reminder
                child_event_ids.extend(schedule_reminders(db, interview))
                child_event_ids.append(schedule_scorecard_reminder(db, interview))
                policy = get_policy(db, interview.owner_id)
                follow_up_sweeps.append((interview.owner_id, (_aware(interview.end_at) or utcnow()) + timedelta(hours=policy.feedback_due_hours), f"feedback:{interview.id}:{interview.reschedule_count}"))
            elif event.operation == "CALENDAR_UPDATE":
                interview = db.get(Interview, event.aggregate_id)
                if not interview:
                    raise RuntimeError("Interview no longer exists")
                event.provider_message_id = _update_event(connection, token, interview)
                interview.status = "SCHEDULED"
                from .interview_ops import get_policy, schedule_reminders, schedule_scorecard_reminder
                child_event_ids.extend(schedule_reminders(db, interview))
                child_event_ids.append(schedule_scorecard_reminder(db, interview))
                policy = get_policy(db, interview.owner_id)
                follow_up_sweeps.append((interview.owner_id, (_aware(interview.end_at) or utcnow()) + timedelta(hours=policy.feedback_due_hours), f"feedback:{interview.id}:{interview.reschedule_count}"))
            elif event.operation == "CALENDAR_CANCEL":
                interview = db.get(Interview, event.aggregate_id)
                if not interview:
                    raise RuntimeError("Interview no longer exists")
                event.provider_message_id = _cancel_event(connection, token, interview)
                application = db.get(Application, interview.application_id)
                if application:
                    application.status = "INTERVIEW_PENDING"
            elif event.operation == "EMAIL_SEND":
                event.provider_message_id = _send_email(connection, token, event.payload or {})
            else:
                raise RuntimeError(f"Unsupported outbox operation: {event.operation}")
            event.status = "COMPLETED"
            event.processed_at = utcnow()
            event.last_error = None
            db.add(AuditLog(owner_id=event.owner_id, application_id=None, action=f"OUTBOX_{event.operation}_COMPLETED",
                            metadata_json={"outbox_id": event.id, "provider_id": event.provider_message_id}))
        except Exception as exc:
            event.last_error = str(exc)[:2000]
            if event.attempts >= event.max_attempts:
                event.status = "FAILED"
            else:
                event.status = "PENDING"
                retry_delay = min(300, 2 ** event.attempts)
                event.available_at = utcnow() + timedelta(seconds=retry_delay)
    for child_event_id in child_event_ids:
        dispatch_outbox(child_event_id)
    if follow_up_sweeps:
        from .interview_ops import schedule_follow_up_sweep
        for owner_id, at, key in follow_up_sweeps:
            schedule_follow_up_sweep(owner_id, at, key)
    if retry_delay and not get_settings().queue_eager:
        settings = get_settings()
        from redis import Redis
        from rq import Queue
        Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url)).enqueue_in(
            timedelta(seconds=retry_delay), process_outbox_event, event_id, job_id=f"outbox:{event_id}:retry"
        )


def dispatch_outbox(event_id: str) -> None:
    settings = get_settings()
    if settings.queue_eager:
        process_outbox_event(event_id)
        return
    from redis import Redis
    from rq import Queue
    queue = Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url))
    with session_scope() as db:
        event = db.get(OutboxEvent, event_id)
        available_at = _aware(event.available_at) if event else None
    if available_at and available_at > utcnow():
        queue.enqueue_at(available_at, process_outbox_event, event_id, job_id=f"outbox:{event_id}", retry=None)
    else:
        queue.enqueue(process_outbox_event, event_id, job_id=f"outbox:{event_id}", retry=None)
