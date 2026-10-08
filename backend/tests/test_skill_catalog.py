"""TalentFlow's side of the shared catalog: database vocabulary, pipeline adapters, import, refresh and search."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.candidate_search import CandidateVersionMatch, rank_candidate_versions
from app.catalog import catalog as shared
from app.cv_warehouse_client import CvWarehouseClient
from app.database import session_scope
from app.embedding_service import EmbeddingRuntimeConfig, EmbeddingService, set_embedding_service
from app.main import app
from app.models import CandidateResumeVersion, Skill
from app.pipeline import KNOWN_SKILLS, analyze_evidence, extract_requirements, find_skill
from app.resume_comparison import canonicalize_skills
from app.skill_catalog import RAW_PREFIX, resolve_filter_keys, seed_skill_catalog, skill_key

client = TestClient(app)


def test_migration_loads_every_catalog_skill_and_reseeding_changes_nothing() -> None:
    with session_scope() as db:
        loaded = db.scalar(select(func.count()).select_from(Skill).where(Skill.catalog_id.is_not(None)))
        assert loaded >= 400
        legacy = {row.canonical_name: row.catalog_id for row in db.scalars(select(Skill)).all()
                  if row.id.startswith("10000000")}
        assert legacy["C"] == "c" and legacy["React Native"] == "react-native" and legacy[".NET"] == "dotnet"
        assert seed_skill_catalog(db.connection()) == {"skills_added": 0, "skills_linked": 0, "aliases_added": 0}


def test_aliases_in_the_database_canonicalize_to_catalog_skills() -> None:
    with session_scope() as db:
        found = {item["raw_value"]: item["canonical_name"] for item in canonicalize_skills(db, ["k8s", "Golang", "ReactJS", "Power BI"])}
        assert found == {"k8s": "Kubernetes", "Golang": "Go", "ReactJS": "React", "Power BI": "Power BI"}


def test_filter_values_resolve_to_catalog_ids_including_database_ids() -> None:
    with session_scope() as db:
        kubernetes_row = db.scalar(select(Skill).where(Skill.catalog_id == "kubernetes"))
        assert resolve_filter_keys(db, ["k8s", "Kubernetes", "kubernetes", kubernetes_row.id, "Totally Unknown"]) == [
            "kubernetes", "raw:totally unknown"]
    assert skill_key("Terraform") == "terraform" and skill_key("???").startswith(RAW_PREFIX)


def test_pipeline_vocabulary_is_a_view_of_the_catalog() -> None:
    assert len(KNOWN_SKILLS) > 300 and {"Go", "Flutter", "Splunk", "dbt"} <= set(KNOWN_SKILLS)
    requirements = extract_requirements("Cybersecurity: SIEM, Splunk, pentest, ISO 27001, CEH, OSCP. Ưu tiên Python.")
    assert {"SIEM", "Splunk", "Penetration Testing", "ISO 27001"} <= set(requirements["required_skills"])
    assert requirements["preferred_skills"] == ["Python"]
    assert requirements["certifications"] == ["CEH", "OSCP"]
    assert requirements["category"] == "CYBERSECURITY"
    assert set(requirements) >= {"required_skills", "preferred_skills", "minimum_experience"}


def test_evidence_is_a_literal_span_and_go_is_not_read_from_google() -> None:
    cv = "Worked at Google. Built services in Go and Python.  Ran MongoDB."
    evidence = {item["requirement"]: item for item in analyze_evidence(cv, ["Go", "Golang", "Rust", "MongoDB"])}
    assert evidence["Go"]["matched"] and "Built services in Go" in evidence["Go"]["evidence"]
    assert evidence["Go"]["evidence"] in " ".join(cv.split())
    assert evidence["Golang"]["matched"], "an alias finds the same skill"
    assert not evidence["Rust"]["matched"]
    assert find_skill("Google and MongoDB only", "Go") is None
    assert find_skill("exotic-widget-lang expert", "exotic-widget-lang"), "unknown criteria still match literally"


def test_category_boosts_but_only_an_explicit_category_filters() -> None:
    def match(version: str, category: str | None) -> CandidateVersionMatch:
        return CandidateVersionMatch(candidate_profile_id=version, resume_version_id=version, version_number=1,
                                     semantic_similarity=0.5, skill_ids=frozenset({"python"}), category=category)

    matches = [match("web", "BACKEND"), match("ml", "AI_ML"), match("unknown", None)]
    boosted = rank_candidate_versions(matches, required_skill_ids=["python"], category="AI_ML", category_weight=0.2)
    assert [item.candidate_profile_id for item in boosted] == ["ml", "unknown", "web"]
    assert boosted[0].category == "AI_ML" and "category" in boosted[0].score_components
    filtered = rank_candidate_versions(matches, category="AI_ML", category_weight=0.2, category_is_hard_filter=True)
    assert [item.candidate_profile_id for item in filtered] == ["ml"]
    plain = rank_candidate_versions(matches)
    assert len(plain) == 3 and "category" not in plain[0].score_components


class TopicEncoder:
    """Maps text to a topic axis so a mobile query lands near mobile CVs."""

    def encode(self, texts, *, normalize_embeddings=True):
        return [[1.0, 0.0] if "mobile" in text.casefold() or "flutter" in text.casefold() else [0.0, 1.0] for text in texts]


def _upload(email: str, name: str, text: str) -> None:
    response = client.post("/api/applications", json={
        "job_id": "job-backend-01", "candidate_name": name, "candidate_email": email, "resume_text": text})
    assert response.status_code == 201, response.text


def test_search_explains_coverage_and_category_and_honours_the_switches() -> None:
    set_embedding_service(EmbeddingService(
        EmbeddingRuntimeConfig(enabled=True, model_name="test/topics", model_path="unused", dimension=2),
        encoder=TopicEncoder()))
    try:
        tag = uuid4().hex[:8]
        _upload(f"mobile-{tag}@example.com", f"Mobile {tag}",
                "Mobile Developer\n3 năm Flutter Dart Firebase Riverpod. Published apps on Google Play.")
        _upload(f"backend-{tag}@example.com", f"Backend {tag}",
                "Backend Developer\n3 năm Python FastAPI PostgreSQL Docker Redis và AWS.")
        query = "Mobile developer: Flutter, Dart, Firebase. 2+ năm kinh nghiệm"
        body = client.post("/api/candidate-profiles/search", json={"query": query, "limit": 50}).json()
        mine = {item["candidate_profile"]["email"]: item for item in body["results"]
                if tag in item["candidate_profile"]["email"]}
        mobile = mine[f"mobile-{tag}@example.com"]
        assert mobile["coverage"]["required"] == 1.0 and mobile["coverage"]["required_total"] == 4  # + the "mobile developer" concept
        assert mobile["missing_skills"] == [] and {"Flutter", "Dart", "Firebase"} <= set(mobile["matched_skills"])
        assert mobile["category"]["candidate"] == "MOBILE" and mobile["category"]["matched"] is True
        assert mobile["category"]["filter"] == "BOOST"
        other = mine[f"backend-{tag}@example.com"]
        assert other["coverage"]["required"] == 0 and other["category"]["matched"] is False
        assert mobile["ranking_score"] > other["ranking_score"]

        off = client.post("/api/candidate-profiles/search", json={
            "query": query, "limit": 50, "filters": {"auto_specialization": False}}).json()["results"]
        assert all(item["category"]["filter"] is None for item in off)

        explicit = client.post("/api/candidate-profiles/search", json={
            "query": query, "limit": 50, "filters": {"specialization": "Mobile Developer"}}).json()["results"]
        assert explicit and all(item["category"]["candidate"] == "MOBILE" for item in explicit)
        assert client.post("/api/candidate-profiles/search", json={
            "query": query, "filters": {"specialization": "nonsense"}}).status_code == 422

        gated = client.post("/api/candidate-profiles/search", json={
            "query": query, "limit": 50, "filters": {"minimum_skill_coverage": 0.5}}).json()["results"]
        assert gated and all(item["coverage"]["required"] >= 0.5 for item in gated)
        assert not any(item["candidate_profile"]["email"] == f"backend-{tag}@example.com" for item in gated)

        by_alias = client.post("/api/candidate-profiles/search", json={
            "query": query, "limit": 50, "filters": {"required_skill_ids": ["firebase", "DART"]}}).json()["results"]
        assert all(item["coverage"]["required"] == 1.0 for item in by_alias)
        assert any(tag in item["candidate_profile"]["email"] for item in by_alias)
    finally:
        set_embedding_service(None)


def test_refresh_updates_imported_versions_in_place(monkeypatch) -> None:
    tag = uuid4().hex[:8]
    checksum = (tag * 8)[:64]
    text = (f"Warehouse {tag}\nwarehouse-{tag}@example.com\nQA Automation Engineer\n"
            "TECHNICAL SKILLS\nTools: Selenium, Playwright, Postman, Jira\nEXPERIENCE\n"
            "Senior QA Engineer | Acme | 01/2020 - 09/2026\n• Wrote Cypress regression suites.")
    remote = {"id": f"cv-{tag}", "full_name": f"Warehouse {tag}", "email": f"warehouse-{tag}@example.com",
              "phone": "", "original_filename": f"qa-{tag}.txt", "file_size": 140, "checksum": checksum,
              "updated_at": "2026-10-05T10:00:00Z", "skills": ["Selenium"], "experience_years": 6,
              "specialization": "QA_AUTOMATION", "job_title": "QA Automation Engineer", "level": "",
              "catalog_version": "an older catalog", "extracted_text": text}

    async def fake_list(self, tenant_id, *, query="", limit=100, offset=0):
        return {"items": [remote] if offset == 0 else [], "limit": limit, "offset": offset}

    async def fake_get(self, tenant_id, cv_id):
        return remote

    async def fake_download(self, tenant_id, cv_id):
        return text.encode()

    monkeypatch.setattr(CvWarehouseClient, "list_cvs", fake_list)
    monkeypatch.setattr(CvWarehouseClient, "get_cv", fake_get)
    monkeypatch.setattr(CvWarehouseClient, "download_cv", fake_download)
    imported = client.post("/api/cv-warehouse/import", json={"cv_ids": [remote["id"]]})
    assert imported.status_code == 200 and imported.json()["summary"]["imported"] == 1, imported.text
    version_id = imported.json()["imported"][0]["resume_version_id"]
    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(CandidateResumeVersion))

    # The warehouse re-extracted with the current catalog since the import.
    remote.update(skills=["Selenium", "Playwright", "Postman", "Jira"], level="Senior",
                  catalog_version=shared.catalog_version())
    refreshed = client.post("/api/cv-warehouse/refresh?reindex=false")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["summary"]["from_warehouse"] >= 1 and refreshed.json()["summary"]["refreshed"] >= 1
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(CandidateResumeVersion)) == before, "no duplicate versions"
        profile = db.get(CandidateResumeVersion, version_id).extraction["profile"]
        assert {"Selenium", "Playwright", "Postman", "Jira"} <= set(profile["skills"])
        assert profile["catalog"]["category"] == "QA_AUTOMATION" and profile["catalog"]["level"] == "Senior"
        assert profile["catalog"]["source"] == "cv_warehouse"
        assert "Senior QA Engineer" in profile["roles"] and profile["responsibilities"]
    again = client.post("/api/cv-warehouse/refresh?reindex=false")
    assert again.json()["summary"]["refreshed"] == 0, "refreshing twice changes nothing"


def test_catalog_endpoint_and_health_expose_the_version() -> None:
    summary = client.get("/api/catalog").json()
    assert summary["version"] == shared.catalog_version() and "skills_in_database" not in summary
    assert summary["fingerprint"] == shared.catalog_fingerprint() and len(summary["fingerprint"]["json_sha256"]) == 64
    assert len(summary["categories"]) == 11
    assert client.get("/api/health").json()["catalog_version"] == shared.catalog_version()


@pytest.mark.parametrize("model_output, expected", [
    (["Golang"], ["Golang"]),
    (["Go", "Golang"], ["Golang"]),
])
def test_model_and_rules_spellings_of_one_skill_are_one_criterion(model_output: list[str], expected: list[str]) -> None:
    from app.llm import _dedupe_by_skill

    assert _dedupe_by_skill(["Go"], model_output) == expected


def test_reenriching_replaces_old_false_positives_but_keeps_names_the_catalog_cannot_judge() -> None:
    from app.candidate_profiles import enrich_profile

    profile = enrich_profile({"skills": ["Go", "Java", "SQL", "Web3 Wizardry"]},
                             "Backend Developer\nTools: JavaScript, PostgreSQL. Worked at Google with MongoDB.")
    assert set(profile["skills"]) == {"Web3 Wizardry", "JavaScript", "PostgreSQL", "MongoDB"}
    assert profile["catalog"]["skill_ids"] == ["javascript", "postgresql", "mongodb"]


def test_the_same_file_for_the_same_candidate_is_not_a_second_version() -> None:
    from app.candidate_profiles import create_resume_version, resolve_candidate_profile

    email = f"dupe-{uuid4().hex[:8]}@example.com"
    with session_scope() as db:
        profile = resolve_candidate_profile(db, owner_id="00000000-0000-0000-0000-000000000001",
                                            name="Dupe Candidate", email=email, phone=None)
        args = dict(profile=profile, storage_key="k", original_filename="a.txt", file_size=3,
                    checksum="d" * 64, extracted_text="Python developer")
        first = create_resume_version(db, **args)
        again = create_resume_version(db, **{**args, "storage_key": "k2"})
        other = create_resume_version(db, **{**args, "checksum": "e" * 64})
        assert again.id == first.id and other.version_number == first.version_number + 1


def test_requirements_never_list_a_skill_twice_and_keep_months_as_half_years() -> None:
    only_preferred = extract_requirements("Ưu tiên Python, Docker, Kafka, Redis, Terraform.")
    assert only_preferred["required_skills"] == ["Python", "Docker", "Kafka"]
    assert only_preferred["preferred_skills"] == ["Redis", "Terraform"]
    months = extract_requirements("Yêu cầu: kinh nghiệm 18 tháng với Python")
    assert months["minimum_experience"] == 1 and months["minimum_experience_years"] == 1.5


def test_explicit_unclassified_category_is_a_valid_filter() -> None:
    response = client.post("/api/candidate-profiles/search", json={
        "query": "Python developer", "limit": 5, "filters": {"specialization": "UNCLASSIFIED"}})
    assert response.status_code == 200, response.text
    assert all(item["category"]["candidate"] is None for item in response.json()["results"])


def test_startup_warns_when_the_warehouse_reads_a_different_catalog(monkeypatch, caplog) -> None:
    import logging
    from app import main

    async def remote(self):
        return {"fingerprint": {"version": "old", "json_sha256": "0" * 64, "py_sha256": "1" * 64}}

    monkeypatch.setattr(CvWarehouseClient, "catalog", remote)
    monkeypatch.setattr(main, "settings", type("S", (), {"cv_warehouse_enabled": True})())
    with caplog.at_level(logging.WARNING):
        import asyncio
        asyncio.run(main.warn_if_catalog_differs_from_warehouse())
    assert "differs from the CV warehouse" in caplog.text
