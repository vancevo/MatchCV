from types import SimpleNamespace

import httpx

from app import resume


def _settings():
    return SimpleNamespace(
        resume_storage_backend="supabase",
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="server-only-key",
        resume_storage_bucket="resumes",
        resume_signed_url_ttl_seconds=300,
        max_upload_mb=10,
    )


def _response(status: int, *, json: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://project.supabase.co/storage/v1")
    return httpx.Response(status, request=request, json=json)


def test_supabase_upload_uses_tenant_prefix_and_service_role(monkeypatch):
    captured = {}
    monkeypatch.setattr(resume, "get_settings", _settings)

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _response(200, json={})

    monkeypatch.setattr(resume.httpx, "post", post)
    resume.store_resume_file("tenant/one", "application-id", "candidate.pdf", b"pdf")

    assert captured["url"].endswith("/object/resumes/tenant%2Fone/application-id/original.pdf")
    assert captured["headers"]["Authorization"] == "Bearer server-only-key"
    assert captured["headers"]["x-upsert"] == "true"


def test_supabase_signed_url_is_short_lived(monkeypatch):
    monkeypatch.setattr(resume, "get_settings", _settings)

    def post(url, **kwargs):
        assert kwargs["json"] == {"expiresIn": 300}
        return _response(200, json={"signedURL": "/object/sign/resumes/path?token=temporary"})

    monkeypatch.setattr(resume.httpx, "post", post)
    url = resume.resume_file_url("tenant", "application-id", "candidate.docx")
    assert url == "https://project.supabase.co/storage/v1/object/sign/resumes/path?token=temporary"


def test_supabase_delete_removes_exact_object(monkeypatch):
    captured = {}
    monkeypatch.setattr(resume, "get_settings", _settings)

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return _response(200, json={})

    monkeypatch.setattr(resume.httpx, "request", request)
    resume.delete_resume_file("tenant", "application-id", "candidate.txt")

    assert captured["method"] == "DELETE"
    assert captured["json"] == {"prefixes": ["tenant/application-id/original.txt"]}


def test_bucket_created_private_when_missing(monkeypatch):
    created = {}
    monkeypatch.setattr(resume, "get_settings", _settings)
    monkeypatch.setattr(resume.httpx, "get", lambda *args, **kwargs: _response(404, json={}))

    def post(url, **kwargs):
        created.update(url=url, **kwargs)
        return _response(200, json={})

    monkeypatch.setattr(resume.httpx, "post", post)
    resume.ensure_resume_bucket()
    assert created["json"]["public"] is False
    assert created["json"]["id"] == "resumes"
