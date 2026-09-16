from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from .config import get_settings


DEV_USER_ID = "00000000-0000-0000-0000-000000000001"


def current_actor(authorization: str | None = Header(default=None)) -> dict[str, str]:
    """Identity of the person behind the request, kept for audit trails."""
    settings = get_settings()
    if not settings.auth_required:
        return {"id": DEV_USER_ID, "email": ""}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    supabase_url = settings.supabase_url
    jwt_secret = settings.supabase_jwt_secret
    try:
        if jwt_secret:
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"], audience="authenticated")
        elif supabase_url:
            jwks = jwt.PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")
            key = jwks.get_signing_key_from_jwt(token)
            payload = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience="authenticated")
        else:
            raise RuntimeError("Supabase auth is not configured")
    except Exception as exc:
        raise HTTPException(401, "Invalid or expired access token") from exc
    user_id = str(payload.get("sub", ""))
    if not user_id:
        raise HTTPException(401, "Access token has no user id")
    return {"id": user_id, "email": str(payload.get("email", ""))}


def current_user_id(authorization: str | None = Header(default=None)) -> str:
    return current_actor(authorization)["id"]


def current_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    user_id: str = Depends(current_user_id),
) -> str:
    """Resolve the data boundary; the user's personal tenant stays backward compatible."""
    tenant_id = (x_tenant_id or user_id).strip()
    if tenant_id == user_id:
        return tenant_id
    from .database import session_scope
    from .models import TenantMembership

    with session_scope() as db:
        member = db.scalar(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "ACTIVE",
        ))
        if not member:
            raise HTTPException(403, "Tenant access denied")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and member.role.upper() == "VIEWER":
            raise HTTPException(403, "Viewer role is read-only")
    return tenant_id


def require_tenant_role(*allowed_roles: str):
    allowed = {role.upper() for role in allowed_roles}

    def dependency(
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        user_id: str = Depends(current_user_id),
    ) -> str:
        tenant_id = (x_tenant_id or user_id).strip()
        if tenant_id == user_id:
            role = "OWNER"
        else:
            from .database import session_scope
            from .models import TenantMembership

            with session_scope() as db:
                member = db.scalar(select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.user_id == user_id,
                    TenantMembership.status == "ACTIVE",
                ))
                if not member:
                    raise HTTPException(403, "Tenant access denied")
                role = member.role.upper()
        if role not in allowed:
            raise HTTPException(403, "Insufficient tenant role")
        return tenant_id

    return dependency
