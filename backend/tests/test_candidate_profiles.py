from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import session_scope
from app.main import app
from app.models import CandidateProfile, CandidateResumeVersion
from app.resume_comparison import canonicalize_skills, normalize_skill


client = TestClient(app)


def _upload(filename: str, text: str) -> dict:
    response = client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files={"files": (filename, text.encode(), "text/plain")},
    )
    assert response.status_code == 202
    return response.json()["items"][0]["application"]


def test_same_email_and_phone_build_one_profile_with_versioned_resumes():
    email = "versioned-candidate@example.com"
    first = _upload(
        "cv-moi-nhat.txt",
        f"NGUYỄN VĂN VERSION\n{email}\n0917 620 483\n3 năm Python FastAPI PostgreSQL.",
    )
    second = _upload(
        "resume-updated.txt",
        f"NGUYỄN VĂN VERSION\n{email.upper()}\n+84 917 620 483\n5 năm Python FastAPI PostgreSQL Docker Redis.",
    )

    assert first["candidate_profile_id"] == second["candidate_profile_id"]
    assert first["resume_version_id"] != second["resume_version_id"]
    assert first["candidate"]["phone"] == second["candidate"]["phone"] == "0917620483"
    assert first["resume_filename"].endswith("_CV_v1.txt")
    assert second["resume_filename"].endswith("_CV_v2.txt")

    profile = client.get(f"/api/candidate-profiles/{first['candidate_profile_id']}")
    assert profile.status_code == 200
    body = profile.json()
    assert body["resume_count"] == 2
    assert body["application_count"] == 2
    assert [item["version"] for item in body["resume_versions"]] == [2, 1]
    assert body["resume_versions"][0]["change"]["kind"] == "CHANGED"
    assert body["resume_versions"][0]["applications"][0]["job_title"] == "Backend Python Developer"
    assert body["resume_versions"][0]["extracted_at"]
    assert body["resume_versions"][0]["extraction"]["profile"]["experience_years"] == 5
    assert "Python" in body["resume_versions"][0]["extraction"]["profile"]["skills"]

    version = body["resume_versions"][0]
    resume = client.get(
        f"/api/candidate-profiles/{body['id']}/resume-versions/{version['id']}"
    )
    assert resume.status_code == 200
    assert resume.json()["filename"].endswith("_CV_v2.txt")
    assert "Docker Redis" in resume.json()["text"]
    assert resume.json()["extraction"] == version["extraction"]

    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(CandidateProfile).where(
            CandidateProfile.normalized_email == email
        )) == 1
        assert db.scalar(select(func.count()).select_from(CandidateResumeVersion).where(
            CandidateResumeVersion.candidate_profile_id == body["id"]
        )) == 2


def test_resume_comparison_is_neutral_and_canonicalizes_skill_aliases():
    email = "comparison-candidate@example.com"
    first = _upload(
        "comparison-v1.txt",
        f"TRẦN VĂN COMPARE\n{email}\n0909 111 222\n4 năm ReactJS TypeScript PostgreSQL.",
    )
    second = _upload(
        "comparison-v2.txt",
        f"TRẦN VĂN COMPARE\n{email}\n0909 111 222\n3 năm React.js TypeScript PostgreSQL Docker.",
    )
    profile_id = first["candidate_profile_id"]
    assert second["candidate_profile_id"] == profile_id

    # The offline rules extractor only knows the current job criteria. Replace the two frozen
    # snapshots with deterministic extractor output to exercise taxonomy-aware version diffing.
    with session_scope() as db:
        versions = list(db.scalars(select(CandidateResumeVersion).where(
            CandidateResumeVersion.candidate_profile_id == profile_id
        ).order_by(CandidateResumeVersion.version_number)))
        versions[0].extraction = {
            **versions[0].extraction,
            "profile": {"skills": ["ReactJS", "TypeScript", "PostgreSQL"],
                        "experience_years": 4, "education": [], "summary": ""},
        }
        versions[1].extraction = {
            **versions[1].extraction,
            "profile": {"skills": ["React.js", "TypeScript", "PostgreSQL", "Docker"],
                        "experience_years": 3, "education": [], "summary": ""},
        }
    rebuild = client.post(f"/api/candidate-profiles/{profile_id}/resume-comparisons/rebuild")
    assert rebuild.status_code == 200
    assert rebuild.json()["rebuilt"] == 1

    response = client.get(f"/api/candidate-profiles/{profile_id}/resume-comparisons")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    comparison = items[0]
    assert comparison["from_version"]["version"] == 1
    assert comparison["to_version"]["version"] == 2
    assert comparison["analysis_method"] == "STRUCTURED_DIFF+SKILL_TAXONOMY"
    assert comparison["summary"]["conflicts_count"] == 1
    assert any(item["category"] == "skills" and item.get("change_type") == "ALIAS_RENAMED"
               and item.get("canonical_value") == "React" for item in comparison["modified"])
    assert any(item["category"] == "experience_years" for item in comparison["conflicts"])
    serialized = str(comparison).casefold()
    assert "tốt hơn" not in serialized
    assert "phát triển" not in serialized

    detail = client.get(
        f"/api/candidate-profiles/{profile_id}/resume-comparisons/{comparison['id']}"
    )
    assert detail.status_code == 200
    assert detail.json() == comparison

    profile = client.get(f"/api/candidate-profiles/{profile_id}").json()
    assert profile["resume_versions"][0]["comparison_id"] == comparison["id"]


def test_skill_taxonomy_preserves_different_technologies():
    assert normalize_skill("C") != normalize_skill("C++")
    assert normalize_skill("Java") != normalize_skill("JavaScript")
    assert normalize_skill("React") != normalize_skill("React Native")
    with session_scope() as db:
        values = canonicalize_skills(db, ["ReactJS", "React.js", "React Native", "Java", "JavaScript"])
        keys = {item["key"] for item in values}
        assert len(values) == 4
        assert len(keys) == 4
