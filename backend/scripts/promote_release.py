"""Fail-closed CI/CD client for TalentFlow's release promotion gate."""
from __future__ import annotations

import json
import os
import sys

import httpx


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    try:
        api_url = required("TALENTFLOW_API_URL").rstrip("/")
        token = required("TALENTFLOW_ACCESS_TOKEN")
        tenant_id = required("TALENTFLOW_TENANT_ID")
        release_version = required("TALENTFLOW_RELEASE_VERSION")
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_id}
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{api_url}/api/operations/release-gates", headers=headers)
            response.raise_for_status()
            gate = next((value for value in response.json() if value["release_version"] == release_version), None)
            if not gate:
                raise RuntimeError(f"No release gate found for {release_version}")
            promoted = client.post(
                f"{api_url}/api/operations/release-gates/{gate['id']}/promote", headers=headers,
            )
            if promoted.status_code != 200:
                detail = promoted.json().get("detail", promoted.text)
                raise RuntimeError(f"Promotion blocked: {detail}")
            result = promoted.json()
            if result.get("status") != "PROMOTED":
                raise RuntimeError(f"Unexpected promotion status: {result.get('status')}")
            print(json.dumps({
                "release_version": result["release_version"], "status": result["status"],
                "promoted_at": result["promoted_at"],
            }))
            return 0
    except Exception as exc:
        print(f"TalentFlow release gate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
