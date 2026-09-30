from app.candidate_search_document import build_candidate_search_document, redact_pii


def test_search_document_is_structured_and_removes_candidate_pii():
    document = build_candidate_search_document({
        "candidate": {
            "name": "Nguyễn Văn A",
            "email": "candidate@example.com",
            "phone": "+84 912 345 678",
        },
        "profile": {
            "skills": ["ReactJS", "TypeScript"],
            "experience_years": 4,
            "education": ["Đại học Bách Khoa"],
            "summary": (
                "Nguyễn Văn A xây ERP bằng React. Liên hệ candidate@example.com "
                "hoặc +84 912 345 678."
            ),
            "domains": ["ERP"],
        },
    }, canonical_skills=["React", "TypeScript"])

    assert "canonical skills: React; TypeScript; ReactJS" in document.text
    assert "experience years: 4" in document.text
    assert "industries: ERP" in document.text
    assert "Nguyễn Văn A" not in document.text
    assert "candidate@example.com" not in document.text
    assert "912 345 678" not in document.text
    assert len(document.content_hash) == 64


def test_same_content_has_stable_hash_and_changed_content_does_not():
    first = build_candidate_search_document({"profile": {"skills": ["Python"]}})
    second = build_candidate_search_document({"profile": {"skills": ["Python"]}})
    changed = build_candidate_search_document({"profile": {"skills": ["Python", "FastAPI"]}})

    assert first.content_hash == second.content_hash
    assert first.content_hash != changed.content_hash


def test_redaction_removes_generic_email_and_phone_without_explicit_values():
    cleaned = redact_pii("Mail me@company.vn / phone 0912-345-678 about Python")
    assert cleaned == "Mail / phone about Python"


def test_search_document_flattens_structured_experience_project_and_education():
    document = build_candidate_search_document({
        "profile": {
            "resume_schema": {
                "experiences": [{"job": "Backend Engineer", "company": "Acme", "description": "Built APIs"}],
                "projects": [{"name": "ERP", "description": "Inventory platform"}],
                "educations": [{"degree": "BSc", "programme": "Computer Science", "name": "HCMUT"}],
            },
        },
    })

    assert "Backend Engineer - Acme - Built APIs" in document.text
    assert "ERP - Inventory platform" in document.text
    assert "BSc - Computer Science - HCMUT" in document.text
