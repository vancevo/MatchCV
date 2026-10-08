"""CV warehouse behaviour built on the shared catalog: extraction, search switches, facets and migrations."""
from __future__ import annotations

import random
import string

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import app.database as database
from app.extraction import extract_metadata
from app.main import app

KEY = {"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"}


def cv_text(name: str, title: str, tools: str, extra: str = "") -> str:
    return (f"{name}\n{title}\nHà Nội | {name.split()[0].lower()}@example.com | Phone: +84 000 000 123\n"
            f"Portfolio: https://example.com/{name.split()[0]} | GitHub: https://example.com/x/code\n"
            f"TECHNICAL SKILLS\nTools: {tools}\nEDUCATION\nBEng, Information Technology | 2015 - 2019\n"
            "Relevant coursework: algorithms, machine learning.\nTRAINING & LANGUAGES\n"
            "Vietnamese: native | English: professional working proficiency.\nEXPERIENCE\n"
            f"Senior {title} | Acme | 07/2022 - 09/2026\n• Led a team of four. {extra}\n")


def test_extraction_uses_whole_tokens_and_fills_the_structured_fields() -> None:
    tools = "Go, JavaScript, MongoDB, PostgreSQL, Kubernetes"
    text = cv_text("An Nguyen", "Backend Engineer", tools, "Shipped to Google Cloud and used SQL daily. CEH certified.")
    found = extract_metadata(text, "an.txt")
    assert {"Go", "JavaScript", "MongoDB", "PostgreSQL", "Kubernetes", "SQL"} <= set(found["skills"])
    assert "Java" not in found["skills"] and "GitHub" not in found["skills"]
    assert found["specialization"] == "BACKEND" and found["level"] == "Senior"
    assert found["education_level"] == "Bachelor" and found["languages"] == ["Vietnamese", "English"]
    assert found["certifications"] == ["CEH"]
    assert "Machine Learning" not in found["skills"], "coursework is not a skill"
    assert extract_metadata("Plain note: we go to Google and MongoDB.", "x.txt")["skills"] == ["MongoDB"]


def test_search_ranks_by_skill_coverage_and_can_run_without_auto_category() -> None:
    tag = "".join(random.choices(string.ascii_lowercase, k=6))  # letters only: a name with digits is not read as a name
    with TestClient(app) as client:
        for name, title, tools in (
            (f"Chi{tag} Nguyen", "Mobile Developer", "Flutter, Dart, Firebase, Riverpod"),
            (f"Dung{tag} Nguyen", "Mobile Developer", "Flutter, Firebase"),
            (f"Em{tag} Nguyen", "Backend Engineer", "Python, FastAPI, PostgreSQL"),
        ):
            created = client.post("/api/v1/cvs", files={"file": (f"{name}.txt", cv_text(name, title, tools).encode(), "text/plain")})
            assert created.status_code == 201, created.text
        query = "Mobile developer with Flutter, Dart and Firebase"

        auto = client.post("/api/v1/cvs/search", headers=KEY, json={"query": query, "limit": 20}).json()
        assert auto["specialization_filter"] is None and auto["specialization_boost"]["code"] == "MOBILE"
        assert auto["results"][0]["specialization"] == "MOBILE"
        # A required skill from another category still finds its CVs: auto-detection must not hide them.
        cross = client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": "backend engineer with Python", "filters": {"required_skills": ["Python"]}, "limit": 50}).json()
        assert any(tag in item["full_name"].lower() for item in cross["results"]), cross["specialization_filter"]

        plain = client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": query, "filters": {"auto_specialization": False}, "limit": 50}).json()
        assert plain["specialization_filter"] is None and plain["detected_specialization"]["code"] == "MOBILE"
        mine = [item for item in plain["results"] if tag in item["full_name"].lower()]
        assert [item["full_name"] for item in mine][:2] == [f"Chi{tag} Nguyen", f"Dung{tag} Nguyen"]
        full, partial = mine[0], mine[1]
        assert full["skill_coverage"] == 1.0 and full["missing_skills"] == []
        assert partial["skill_coverage"] < 1.0 and "Dart" in partial["missing_skills"]
        assert {"Flutter", "Firebase"} <= set(partial["matched_skills"])
        assert plain["query_skills"]["required"][:3] == ["Mobile App Development", "Flutter", "Dart"]

        explicit = client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": "any", "filters": {"required_skills": ["firebase", "DART"], "minimum_skill_coverage": 0.5,
                                       "auto_specialization": False}, "limit": 50}).json()
        names = {item["full_name"] for item in explicit["results"] if tag in item["full_name"].lower()}
        assert names == {f"Chi{tag} Nguyen", f"Dung{tag} Nguyen"}, "alias names resolve and 50% coverage lets the partial CV in"
        strict = client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": "any", "filters": {"required_skills": ["Dart", "Firebase"], "auto_specialization": False}}).json()
        assert {item["full_name"] for item in strict["results"] if tag in item["full_name"].lower()} == {f"Chi{tag} Nguyen"}


def test_reextract_filters_and_catalog_endpoints() -> None:
    with TestClient(app) as client:
        client.post("/api/v1/cvs", files={"file": ("old.txt", cv_text("Old Record", "QA Engineer", "Selenium, Postman").encode(), "text/plain")})
        listed = client.get("/api/v1/cvs", headers=KEY, params={"q": "Old Record"}).json()["items"][0]
        client.patch(f"/api/v1/cvs/{listed['id']}", json={"skills": ["java"], "level": "", "certifications": []})
        assert set(client.get(f"/api/v1/cvs/{listed['id']}", headers=KEY).json()["edited_fields"]) == {"skills", "level", "certifications"}
        rerun = client.post("/api/v1/cvs/reextract").json()
        assert rerun["total"] >= 1
        kept = client.get(f"/api/v1/cvs/{listed['id']}", headers=KEY).json()
        assert kept["skills"] == ["java"] and kept["level"] == "", "a hand correction survives re-extraction"
        assert kept["specialization"] == "QA_AUTOMATION", "fields nobody edited are still refreshed"
        assert client.post("/api/v1/cvs/reextract").json()["changed"] == 0

        facets = client.get("/api/v1/filters").json()
        assert facets["skill_facets"] and {"id", "name", "kind", "count"} <= set(facets["skill_facets"][0])
        assert "Senior" in facets["levels"] and facets["catalog_version"]
        summary = client.get("/api/v1/catalog").json()
        assert summary["entries"] >= 350 and len(summary["categories"]) == 11
        assert client.get("/api/v1/ready").json()["catalog_version"] == summary["version"]
        assert client.get("/api/v1/health").json()["catalog_version"] == summary["version"]


def test_startup_migration_adds_missing_columns_and_is_repeatable(monkeypatch, tmp_path) -> None:
    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as connection:
        connection.execute(text("CREATE TABLE cv_documents (id VARCHAR(36) PRIMARY KEY, full_name VARCHAR(200))"))
        connection.execute(text("INSERT INTO cv_documents VALUES ('a', 'Existing')"))
    monkeypatch.setattr(database, "engine", old)
    assert database.ensure_columns() == ["cv_documents.level", "cv_documents.certifications", "cv_documents.edited_fields"]
    assert database.ensure_columns() == []
    assert {"level", "certifications"} <= {column["name"] for column in inspect(old).get_columns("cv_documents")}
    with old.connect() as connection:
        assert connection.execute(text("SELECT level, certifications FROM cv_documents")).one() == ("", None)


def test_unedited_cv_is_fully_refreshed_and_explicit_unclassified_is_accepted() -> None:
    with TestClient(app) as client:
        client.post("/api/v1/cvs", files={"file": ("fresh.txt", cv_text("Fresh Record", "QA Engineer", "Selenium, Playwright").encode(), "text/plain")})
        row = client.get("/api/v1/cvs", headers=KEY, params={"q": "Fresh Record"}).json()["items"][0]
        client.post("/api/v1/cvs/reextract")
        assert client.get(f"/api/v1/cvs/{row['id']}", headers=KEY).json()["edited_fields"] == []
        assert client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": "x y", "filters": {"specialization": "UNCLASSIFIED"}}).status_code == 200
        assert client.post("/api/v1/cvs/search", headers=KEY, json={
            "query": "x y", "filters": {"specialization": "nope"}}).status_code == 422


def test_catalog_endpoint_reports_the_hashes_of_the_files_it_loaded() -> None:
    import hashlib
    from app.catalog import catalog as shared

    with TestClient(app) as client:
        body = client.get("/api/v1/catalog").json()
        assert body["fingerprint"]["json_sha256"] == hashlib.sha256(shared.DATA_FILE.read_bytes()).hexdigest()
        assert client.get("/api/v1/ready").json()["catalog_sha256"] == body["fingerprint"]
