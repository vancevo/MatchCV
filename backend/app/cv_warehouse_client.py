from __future__ import annotations

import httpx

from .config import get_settings


class CvWarehouseError(RuntimeError):
    pass


class CvWarehouseClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.cv_warehouse_enabled
        self.base_url = settings.cv_warehouse_api_url
        self.api_key = settings.cv_warehouse_api_key
        self.timeout = settings.cv_warehouse_timeout_seconds

    def _headers(self, tenant_id: str) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "X-Tenant-ID": tenant_id}

    def _assert_configured(self) -> None:
        if not self.enabled:
            raise CvWarehouseError("Kho CV chưa được bật trong cấu hình TalentFlow")
        if not self.base_url or not self.api_key:
            raise CvWarehouseError("Kho CV thiếu API URL hoặc API key")

    async def search(self, tenant_id: str, payload: dict) -> dict:
        self._assert_configured()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/cvs/search", json=payload, headers=self._headers(tenant_id),
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CvWarehouseError(f"Không thể tìm kiếm Kho CV: {exc}") from exc

    async def list_cvs(self, tenant_id: str, *, query: str = "", limit: int = 100, offset: int = 0) -> dict:
        self._assert_configured()
        try:
            params = {"limit": limit, "offset": offset}
            if query.strip():
                params["q"] = query.strip()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/cvs", params=params, headers=self._headers(tenant_id),
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CvWarehouseError(f"Không thể đọc danh sách Kho CV: {exc}") from exc

    async def get_cv(self, tenant_id: str, cv_id: str) -> dict:
        self._assert_configured()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/cvs/{cv_id}", headers=self._headers(tenant_id),
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CvWarehouseError(f"Không thể đọc CV {cv_id}: {exc}") from exc

    async def download_cv(self, tenant_id: str, cv_id: str) -> bytes:
        self._assert_configured()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/cvs/{cv_id}/download", headers=self._headers(tenant_id),
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise CvWarehouseError(f"Không thể tải file CV {cv_id}: {exc}") from exc

    async def status(self, tenant_id: str) -> dict:
        self._assert_configured()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/v1/ready", headers=self._headers(tenant_id))
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CvWarehouseError(f"Kho CV không sẵn sàng: {exc}") from exc
