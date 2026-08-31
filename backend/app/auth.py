from __future__ import annotations

import os

import jwt
from fastapi import Header, HTTPException


DEV_USER_ID = "00000000-0000-0000-0000-000000000001"


def current_user_id(authorization: str | None = Header(default=None)) -> str:
    if os.getenv("AUTH_REQUIRED", "false").lower() not in {"1", "true", "yes"}:
        return DEV_USER_ID
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
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
    return user_id
