from __future__ import annotations

import json
import os
import urllib.request


def has_master_seed_config() -> bool:
    return all(os.getenv(name, "").strip() for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "MASTER_EMAIL", "MASTER_PASSWORD"))


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def supabase_request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    supabase_url = required_env("SUPABASE_URL").rstrip("/")
    service_role_key = required_env("SUPABASE_SERVICE_ROLE_KEY")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{supabase_url}{path}",
        data=body,
        method=method,
        headers={
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read().decode("utf-8")
        return json.loads(data) if data else {}


def find_user_by_email(email: str) -> dict | None:
    page = 1
    while True:
        result = supabase_request(f"/auth/v1/admin/users?page={page}&per_page=100")
        users = result.get("users", [])
        for user in users:
            if user.get("email", "").lower() == email.lower():
                return user
        if len(users) < 100:
            return None
        page += 1


def create_master_user() -> dict:
    email = required_env("MASTER_EMAIL")
    password = required_env("MASTER_PASSWORD")
    name = os.getenv("MASTER_FULL_NAME", "Master Admin").strip() or "Master Admin"

    existing = find_user_by_email(email)
    if existing:
        return existing

    return supabase_request(
        "/auth/v1/admin/users",
        "POST",
        {
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": name, "role": "master"},
            "app_metadata": {"role": "master"},
        },
    )
