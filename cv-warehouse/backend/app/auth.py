from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import jwt
from fastapi import Header, HTTPException
from sqlalchemy import select

from .config import get_settings
from .database import session_scope
from .models import ServiceApiKey


def _bearer_principal(authorization: str) -> dict[str, str]:
    settings = get_settings()
    token = authorization.removeprefix("Bearer ").strip()
    try:
        if settings.supabase_jwt_secret:
            payload = jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
        elif settings.supabase_url:
            client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")
            key = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience="authenticated")
        else:
            raise RuntimeError("Supabase Auth chưa được cấu hình")
    except Exception as exc:
        raise HTTPException(401, "Access token không hợp lệ hoặc đã hết hạn") from exc
    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise HTTPException(401, "Access token không có user id")
    app_metadata = payload.get("app_metadata") if isinstance(payload.get("app_metadata"), dict) else {}
    tenant_id = str(app_metadata.get("tenant_id") or user_id)
    role = str(app_metadata.get("role") or "ADMIN").upper()
    if role not in {"OWNER", "ADMIN", "VIEWER"}:
        role = "VIEWER"
    return {"actor_id": user_id, "tenant_id": tenant_id, "kind": "USER", "role": role, "scopes": "*"}


def principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict[str, str]:
    settings = get_settings()
    if authorization and authorization.startswith("Bearer "):
        return _bearer_principal(authorization)
    if x_api_key:
        if settings.bootstrap_api_key and secrets.compare_digest(x_api_key, settings.bootstrap_api_key):
            return {"actor_id": "bootstrap-service", "tenant_id": settings.default_tenant_id,
                    "kind": "SERVICE", "role": "SERVICE", "scopes": "cvs.search,cvs.read,cvs.download"}
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        with session_scope() as db:
            value = db.scalar(select(ServiceApiKey).where(
                ServiceApiKey.key_hash == key_hash, ServiceApiKey.status == "ACTIVE",
            ))
            if value:
                value.last_used_at = datetime.now(timezone.utc)
                return {"actor_id": value.id, "tenant_id": value.tenant_id, "kind": "SERVICE", "role": "SERVICE",
                        "scopes": ",".join(value.scopes or [])}
        raise HTTPException(401, "API key không hợp lệ hoặc đã bị thu hồi")
    if not settings.auth_required:
        return {"actor_id": "local-admin", "tenant_id": x_tenant_id or settings.default_tenant_id,
                "kind": "USER", "role": "ADMIN", "scopes": "*"}
    raise HTTPException(401, "Yêu cầu đăng nhập hoặc API key")


def require_scope(value: dict[str, str], scope: str) -> None:
    if value["scopes"] != "*" and scope not in value["scopes"].split(","):
        raise HTTPException(403, f"Thiếu scope {scope}")


def require_admin(value: dict[str, str]) -> None:
    if value.get("kind") != "USER" or value.get("role") not in {"OWNER", "ADMIN"}:
        raise HTTPException(403, "Chỉ OWNER hoặc ADMIN được thay đổi Kho CV")
