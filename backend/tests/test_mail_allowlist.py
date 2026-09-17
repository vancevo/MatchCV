"""The sandbox must reach every address the recruiter actually invited, not one mailbox family."""
from fastapi.testclient import TestClient

from app.main import app
from app.models import TenantPolicy
from app.scheduling import allow_sandbox_recipient, mail_sandbox_recipient_allowed, sandbox_allowlist


client = TestClient(app)


def _policy(**kwargs) -> TenantPolicy:
    policy = TenantPolicy(owner_id="t", mail_sandbox_enabled=True,
                          mail_sandbox_base_email="base@gmail.com", mail_sandbox_max_alias=100,
                          mail_sandbox_allowed_emails=[])
    for key, value in kwargs.items():
        setattr(policy, key, value)
    return policy


def test_base_mailbox_and_its_aliases_still_pass():
    policy = _policy()
    assert mail_sandbox_recipient_allowed(policy, "base@gmail.com")
    assert mail_sandbox_recipient_allowed(policy, "base+7@gmail.com")
    assert not mail_sandbox_recipient_allowed(policy, "base+101@gmail.com")


def test_an_unrelated_address_passes_once_it_is_on_the_allowlist():
    policy = _policy()
    # This was the whole failure: inviting anyone outside the base family blocked the invitation.
    assert not mail_sandbox_recipient_allowed(policy, "ung.vien@congty.vn")
    assert allow_sandbox_recipient(policy, "ung.vien@congty.vn") is True
    assert mail_sandbox_recipient_allowed(policy, "ung.vien@congty.vn")


def test_allowing_is_idempotent_and_case_insensitive():
    policy = _policy()
    allow_sandbox_recipient(policy, "Ung.Vien@CongTy.VN")
    assert sandbox_allowlist(policy) == ["ung.vien@congty.vn"]
    # Already reachable, so nothing to add and no duplicate row.
    assert allow_sandbox_recipient(policy, "ung.vien@congty.vn") is False
    assert allow_sandbox_recipient(policy, "base+2@gmail.com") is False
    assert sandbox_allowlist(policy) == ["ung.vien@congty.vn"]


def test_rubbish_is_never_added():
    policy = _policy()
    assert allow_sandbox_recipient(policy, "khong-phai-email") is False
    assert allow_sandbox_recipient(policy, "") is False
    assert sandbox_allowlist(policy) == []


def test_a_disabled_sandbox_allows_everything():
    assert mail_sandbox_recipient_allowed(_policy(mail_sandbox_enabled=False), "ai.do@example.com")


def test_endpoints_add_then_remove_an_address():
    client.put("/api/mail-sandbox", json={"enabled": True, "base_email": "base@gmail.com", "max_alias": 100})
    added = client.post("/api/mail-sandbox/allowed-emails", json={"email": "Nguoi.Moi@Example.com"})
    assert added.status_code == 200
    assert "nguoi.moi@example.com" in added.json()["allowed_emails"]

    removed = client.delete("/api/mail-sandbox/allowed-emails/nguoi.moi@example.com")
    assert removed.status_code == 200
    assert "nguoi.moi@example.com" not in removed.json()["allowed_emails"]


def test_endpoint_refuses_something_that_is_not_an_address():
    assert client.post("/api/mail-sandbox/allowed-emails", json={"email": "bla"}).status_code == 422


def test_inviting_a_candidate_clears_their_address_for_delivery():
    client.put("/api/mail-sandbox", json={"enabled": True, "base_email": "base@gmail.com", "max_alias": 100})
    application = client.post("/api/applications", json={
        "job_id": "job-backend-01", "candidate_name": "Ứng Viên Ngoài",
        "candidate_email": "ngoai.danh.sach@congty.vn",
        "resume_text": "5 năm Python FastAPI PostgreSQL Docker Redis REST API và vận hành production.",
    }).json()
    assert client.post(f"/api/applications/{application['id']}/scheduling-invitations",
                       json={"timezone_name": "Asia/Ho_Chi_Minh"}).status_code == 201
    # Without this the system queues an invitation it has already forbidden itself to deliver.
    assert "ngoai.danh.sach@congty.vn" in client.get("/api/mail-sandbox").json()["allowed_emails"]


def test_an_auto_added_address_records_which_candidate_it_was_for():
    from app.scheduling import sandbox_allowlist_entries

    class Policy:
        mail_sandbox_enabled = True
        mail_sandbox_base_email = "base@gmail.com"
        mail_sandbox_max_alias = 100
        mail_sandbox_allowed_emails: list = []

    policy = Policy()
    allow_sandbox_recipient(policy, "ai.do@congty.vn", source="INTERVIEW", candidate_name="LÊ VĂN A")
    allow_sandbox_recipient(policy, "tu.them@congty.vn")
    entries = {entry["email"]: entry for entry in sandbox_allowlist_entries(policy)}
    # The recruiter has to be able to see the system picked the right address, not just that it picked one.
    assert entries["ai.do@congty.vn"]["source"] == "INTERVIEW"
    assert entries["ai.do@congty.vn"]["candidate"] == "LÊ VĂN A"
    assert entries["ai.do@congty.vn"]["added_at"].endswith("Z")
    assert entries["tu.them@congty.vn"]["source"] == "MANUAL"


def test_plain_string_rows_from_before_are_still_read():
    from app.scheduling import sandbox_allowlist, sandbox_allowlist_entries

    class Policy:
        mail_sandbox_enabled = True
        mail_sandbox_base_email = "base@gmail.com"
        mail_sandbox_max_alias = 100
        mail_sandbox_allowed_emails = ["Cu@Example.com", {"email": "moi@example.com", "source": "INTERVIEW"}]

    policy = Policy()
    assert sandbox_allowlist(policy) == ["cu@example.com", "moi@example.com"]
    assert sandbox_allowlist_entries(policy)[0]["source"] == "MANUAL"
    assert mail_sandbox_recipient_allowed(policy, "cu@example.com")
