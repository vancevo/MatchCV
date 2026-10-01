from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from .config import get_settings


DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

# Local-only stand-ins for a maker/checker pair, used when AUTH_REQUIRED=false so the two-role
# job-approval flow (Leader submits, HR signs off) can be exercised without a real Supabase project.
# LEADER owns the tenant (their own jobs/data); HR is seeded as an ADMIN member of that same tenant
# so they can act on it via X-Tenant-ID without owning it themselves.
LEADER_USER_ID = DEV_USER_ID
HR_USER_ID = "00000000-0000-0000-0000-000000000002"
LOCAL_ACTORS: dict[str, dict[str, str]] = {
    "leader": {"id": LEADER_USER_ID, "email": "leader@talentflow.local", "name": "Vinh Nguyễn"},
    "hr": {"id": HR_USER_ID, "email": "hr@talentflow.local", "name": "HR Admin"},
}


def current_actor(
    authorization: str | None = Header(default=None),
    x_local_actor: str | None = Header(default=None, alias="X-Local-Actor"),
) -> dict[str, str]:
    """Identity of the person behind the request, kept for audit trails."""
    settings = get_settings()
    if not settings.auth_required:
        local = LOCAL_ACTORS.get((x_local_actor or "leader").strip().lower(), LOCAL_ACTORS["leader"])
        return {"id": local["id"], "email": local["email"]}
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


def current_user_id(
    authorization: str | None = Header(default=None),
    x_local_actor: str | None = Header(default=None, alias="X-Local-Actor"),
) -> str:
    return current_actor(authorization, x_local_actor)["id"]


def resolve_role(db, x_tenant_id: str | None, user_id: str) -> tuple[str, str]:
    """The data boundary (tenant_id) and the caller's role within it. Self-tenant stays OWNER,
    matching the pre-multi-tenant behavior; anyone else must have an active membership row."""
    tenant_id = (x_tenant_id or user_id).strip()
    if tenant_id == user_id:
        return tenant_id, "OWNER"
    from .models import TenantMembership

    member = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.user_id == user_id,
        TenantMembership.status == "ACTIVE",
    ))
    if not member:
        raise HTTPException(403, "Tenant access denied")
    return tenant_id, member.role.upper()


def current_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    user_id: str = Depends(current_user_id),
) -> str:
    from .database import session_scope

    with session_scope() as db:
        tenant_id, role = resolve_role(db, x_tenant_id, user_id)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and role == "VIEWER":
            raise HTTPException(403, "Viewer role is read-only")
    return tenant_id


def current_actor_role(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    user_id: str = Depends(current_user_id),
) -> str:
    """Just the caller's role in the resolved tenant, for actions that gate on role rather than
    on tenant membership alone (e.g. only HR/ADMIN may give final sign-off on job criteria)."""
    from .database import session_scope

    with session_scope() as db:
        _, role = resolve_role(db, x_tenant_id, user_id)
    return role


def require_tenant_role(*allowed_roles: str):
    allowed = {role.upper() for role in allowed_roles}

    def dependency(
        x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
        user_id: str = Depends(current_user_id),
    ) -> str:
        from .database import session_scope

        with session_scope() as db:
            tenant_id, role = resolve_role(db, x_tenant_id, user_id)
        if role not in allowed:
            raise HTTPException(403, "Insufficient tenant role")
        return tenant_id

    return dependency
