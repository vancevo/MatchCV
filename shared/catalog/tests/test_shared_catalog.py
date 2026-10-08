"""Contract tests for the shared catalog. `shared/catalog/sync.py` copies this file into every service,
so both TalentFlow and the CV warehouse run exactly the same checks against their own embedded copy."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from app.catalog import catalog as shared

EMBEDDED = Path(shared.__file__).resolve().parent


def _shared_directory() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "shared" / "catalog" / "catalog.json").exists():
            return parent / "shared" / "catalog"
    return None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["catalog.py", "catalog.json"])
def test_embedded_copy_matches_the_shared_source(name: str) -> None:
    source = _shared_directory()
    if source is None:
        pytest.skip("shared/catalog is not part of this build context")
    assert _digest(EMBEDDED / name) == _digest(source / name), (
        f"{name} drifted from shared/catalog: run `python shared/catalog/sync.py`"
    )


def test_catalog_size_and_integrity() -> None:
    catalog = shared.load_catalog()
    assert catalog.version
    assert 350 <= len(catalog.entries) <= 500
    assert len({entry.id for entry in catalog.entries}) == len(catalog.entries)
    assert len(catalog.categories) == 11 and catalog.categories[-1].code == "FRONTEND"
    assert {"language", "framework", "tool", "database", "cloud", "concept", "soft_skill", "certification",
            "level", "education", "spoken_language", "domain"} <= {entry.kind for entry in catalog.entries}
    for entry in catalog.entries:
        owners = {catalog.by_alias[alias].id for alias in entry.aliases}
        assert owners == {entry.id}, f"{entry.id}: an alias belongs to another entry"


def names(text: str) -> list[str]:
    return [entry.name for entry in shared.extract_skills(text)]


@pytest.mark.parametrize("text, expected, absent", [
    ("Worked at Google with MongoDB and went to Mountain View", ["MongoDB"], ["Go"]),
    ("Python, Go, Rust", ["Python", "Go", "Rust"], []),
    ("Cần biết Go hoặc Java", ["Go", "Java"], []),
    ("We go to market in Q3", [], ["Go"]),
    ("JavaScript and TypeScript developer", ["JavaScript", "TypeScript"], ["Java"]),
    ("Tuned PostgreSQL and MySQL indexes", ["PostgreSQL", "MySQL"], ["SQL"]),
    ("Writes SQL queries daily", ["SQL"], []),
    ("SQL optimization for reporting", ["SQL Optimization"], ["SQL"]),
    ("Knowledge of R, SQL and statistics", ["R", "SQL", "Statistics"], []),
    ("Our R&D team", [], ["R"]),
    ("Tools: C/C++ and C#", ["C", "C++", "C#"], []),
    ("Plan C is ready", [], ["C"]),
    ("Kỹ năng: Node.js, CI/CD, ASP.NET, TCP/IP", ["Node.js", "CI/CD", "ASP.NET", "TCP/IP"], []),
    ("Built with Express and Node.js middleware", ["Node.js", "Express.js"], []),
    ("Express delivery was fast", [], ["Express.js"]),
    ("Swift applications for iPhone with Xcode", ["Swift"], []),
    ("Swift delivery of goods", [], ["Swift"]),
    ("a Spark of creativity", [], ["Apache Spark"]),
    ("Spark and Hadoop jobs", ["Apache Spark", "Hadoop"], []),
    ("see example.net and foo.js", [], [".NET", "JavaScript"]),
    ("GitHub: https://example.com/candidate/code", [], ["GitHub"]),
])
def test_ambiguous_and_punctuated_names(text: str, expected: list[str], absent: list[str]) -> None:
    found = names(text)
    for name in expected:
        assert name in found, f"{name} missing from {found} for {text!r}"
    for name in absent:
        assert name not in found, f"{name} must not be read from {text!r}: {found}"


def test_vietnamese_diacritics_are_optional() -> None:
    assert "Machine Learning" in names("Có kinh nghiệm học máy và xử lý ngôn ngữ tự nhiên")
    assert "Machine Learning" in names("Co kinh nghiem hoc may")
    assert "NLP" in names("xu ly ngon ngu tu nhien")


def test_longest_match_wins_and_spans_are_literal() -> None:
    text = "Experience with Spring Boot and REST API design"
    mentions = shared.find_mentions(text)
    assert [m.entry.name for m in mentions] == ["Spring Boot", "REST API"]
    assert text[mentions[0].start:mentions[0].end] == "Spring Boot"


@pytest.mark.parametrize("text, years", [
    ("Yêu cầu 2+ năm kinh nghiệm Python", 2),
    ("2-4 years of experience with Java", 2),
    ("Ít nhất 3 năm kinh nghiệm", 3),
    ("Tối thiểu 4 năm kinh nghiệm backend", 4),
    ("At least 5 years of experience", 5),
    ("minimum of 3 years experience in QA", 3),
    ("Five years of experience building APIs", 5),
    ("Không yêu cầu kinh nghiệm, biết Python là được", 0),
    ("Our company has 12 years of history", 0),
])
def test_minimum_experience(text: str, years: int) -> None:
    assert shared.extract_jd_requirements(text)["minimum_experience"] == years


def test_required_and_preferred_in_vietnamese() -> None:
    result = shared.extract_jd_requirements("""Backend Developer
Yêu cầu:
- Thành thạo Python, FastAPI, PostgreSQL
- Có kinh nghiệm với Docker

Ưu tiên:
- Kubernetes, Terraform

Redis là lợi thế. Ưu tiên ứng viên biết Kafka.
Quyền lợi: học AWS miễn phí""")
    assert result["required_skills"] == ["Python", "FastAPI", "PostgreSQL", "Docker"]
    assert result["preferred_skills"] == ["Kubernetes", "Terraform", "Redis", "Kafka"]


def test_required_and_preferred_in_english() -> None:
    result = shared.extract_jd_requirements("""Data Engineer
Must have: SQL, Python and Airflow.
Nice to have: dbt, Spark.
Experience with Snowflake is a plus.
Terraform knowledge would be appreciated.
We offer: Kubernetes training.""")
    assert result["required_skills"] == ["SQL", "Python", "Airflow"]
    assert result["preferred_skills"] == ["dbt", "Apache Spark", "Snowflake", "Terraform"]
    assert result["category"] == "DATA_ENGINEERING"


def test_negated_and_ignored_sections_do_not_create_criteria() -> None:
    result = shared.extract_jd_requirements("""Developer
Python required. Java is not needed. Không yêu cầu biết Docker.
About us: we run on Kubernetes and Kafka.""")
    assert result["required_skills"] == ["Python"]
    assert result["preferred_skills"] == []


def test_job_description_fields() -> None:
    result = shared.extract_jd_requirements(
        "Senior QA Automation Engineer\nYêu cầu: Selenium, Postman, JMeter, Jira. ISTQB là lợi thế. "
        "Bachelor's degree. IELTS 6.5. 4+ years of experience.")
    assert result["category"] == "QA_AUTOMATION"
    assert result["level"] == "Senior"
    assert result["education"] == "Bachelor"
    assert result["languages"] == ["English"]
    assert result["certifications"] == ["ISTQB"]
    assert result["minimum_experience"] == 4
    assert {"Selenium", "Postman", "JMeter", "Jira"} <= set(result["required_skills"])


def test_cv_profile_reads_skills_level_education_and_languages() -> None:
    profile = shared.extract_cv_profile("""Nguyễn An
Junior Data Engineer
Hà Nội | an@example.com | Phone: 0900000000
TECHNICAL SKILLS
Tools: Python, SQL, Airflow, dbt, Git
EDUCATION
BEng, Information Technology | 2019 - 2023
Relevant coursework: machine learning, algorithms
TRAINING & LANGUAGES
Vietnamese: native | English: professional proficiency. IELTS 7.0
EXPERIENCE
Junior Data Engineer | Acme | 07/2023 - 09/2026
• Built ETL pipelines in Airflow.
""", "Junior Data Engineer")
    assert profile["category"] == "DATA_ENGINEERING"
    assert profile["level"] == "Junior"
    assert profile["education_level"] == "Bachelor"
    assert profile["languages"] == ["Vietnamese", "English"]
    assert {"Python", "SQL", "Airflow", "dbt", "Git", "ETL"} <= set(profile["skills"])
    assert "Machine Learning" not in profile["skills"], "coursework is not a skill claim"


def test_taxonomy_classifier_and_query_routing() -> None:
    assert shared.classify("Mobile Developer", "Android Kotlin") == "MOBILE"
    assert shared.classify("", "no relevant words here") == shared.UNCLASSIFIED
    assert shared.normalize_code("Cloud Engineer") == "CLOUD"
    assert shared.detect_for_query("Python developer") is None


def test_provider_hook_can_refine_but_never_breaks_the_rules_output() -> None:
    class Provider:
        def refine_jd(self, text: str, draft: dict) -> dict:
            return {"required_skills": ["golang", "Brand New Tool"], "unknown_key": 1}

    refined = shared.extract_jd_requirements("Cần Python. Ưu tiên Docker.", Provider())
    assert refined["required_skills"] == ["Go", "Brand New Tool"]
    assert refined["required_skill_ids"] == ["go"]
    assert refined["preferred_skills"] == ["Docker"]
    assert "unknown_key" not in refined


def test_extraction_is_fast_enough_for_upload_time() -> None:
    text = ("Senior backend engineer with Python, FastAPI, PostgreSQL, Docker, Kubernetes and AWS. " * 60)
    shared.extract_skills(text)  # build the matcher once
    started = time.perf_counter()
    for _ in range(20):
        shared.extract_cv_profile(text)
    assert (time.perf_counter() - started) / 20 < 0.05


# ---- regression cases found by the independent audit --------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Kinh nghiệm: 3-5 năm", 3), ("Kinh nghiệm 1-2 năm", 1), ("Experience: 4-6 years", 4),
    ("- Kinh nghiệm từ 1 năm", 1), ("Requirements:\n- 2 years in a SOC environment", 2),
    ("Requirements: 3 years", 3), ("Requirements:\n- Python\nExperience: 4-6 years", 4),
    ("Yêu cầu: kinh nghiệm 6 tháng", 0.5), ("Requirements: 18 months experience in QA", 1.5),
    ("Requirements: 2 yoe", 2), ("The platform is 5 years old", 0), ("Hợp đồng 2 năm", 0),
])
def test_years_statements_the_audit_found_missing(text: str, expected: float) -> None:
    assert shared.extract_jd_requirements(text)["minimum_experience"] == expected


@pytest.mark.parametrize("text", ["a" * 60_000, "." * 60_000, "1" * 60_000 + " years of experience",
                                  "ệ" * 50_000, "Python, " * 7_000, "C " * 30_000, "Python\n" * 10_000,
                                  "- " * 30_000, "(" * 30_000])
def test_pathological_input_stays_fast(text: str) -> None:
    for read in (shared.extract_jd_requirements, shared.extract_cv_profile):
        started = time.perf_counter()
        read(text)
        assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("text, absent", [
    ("Databricks, Key Vault, ADLS", "Vault"),
    ("Flutter apps, cross-platform and ứng dụng đa nền tảng", "KMP"),
    ("Tự động hóa phát hành ứng dụng", "App Store"),
    ("event-driven services", "Message Queue"),
    ("Security operations coursework", "SOC"),
    ("Cypress tree in the garden", "Cypress"),
    ("Excel in math and excel at teamwork", "Excel"),
    ("Employer: Oracle Corporation", "Oracle Database"),
    ("Solution Architect with Cloud Architect experience", "Lead"),
    ("Master class training, degree incomplete", "Master"),
    ("Pillow, Volatility and Prophet", "OpenCV"),
])
def test_false_positives_the_audit_counted(text: str, absent: str) -> None:
    found = {mention.entry.name for mention in shared.find_mentions(text)}
    assert absent not in found, found


def test_certification_needs_the_word_certificate_or_an_exam_code() -> None:
    assert shared.detect_certifications("Google Data Analytics coursework") == []
    assert shared.detect_certifications("Google Data Analytics Professional Certificate") == ["Google Data Analytics Certificate"]
    assert shared.detect_certifications("AZ-104 and CEH") == ["Azure AZ-104", "CEH"]


def test_education_in_progress_is_not_a_degree() -> None:
    assert shared.detect_education("Master of Science (incomplete), BEng 2019") .name == "Bachelor"
    assert shared.detect_education("MSc, currently pursuing a PhD").name == "Master"


@pytest.mark.parametrize("text, wanted", [
    ("AI/ML engineer", ["Artificial Intelligence", "Machine Learning"]),
    ("C++17 and VPCs", ["C++", "AWS VPC"]),
    ("Experience with Kuber-\nnetes and Post-\ngreSQL", ["Kubernetes", "PostgreSQL"]),
    ("Giám sát hệ thống bằng Prometheus, theo dõi metrics và log", ["Monitoring"]),
])
def test_false_negatives_the_audit_found(text: str, wanted: list[str]) -> None:
    found = names(text)
    assert all(name in found for name in wanted), found


def test_security_monitoring_is_not_ops_monitoring() -> None:
    assert "Monitoring" not in names("Giám sát an ninh hệ thống qua SIEM")


def test_company_context_is_not_a_requirement() -> None:
    result = shared.extract_jd_requirements("""Backend Developer
Công ty tích hợp với Google Cloud và MongoDB Atlas.
Our team uses Spark and Hadoop for analytics, but you will mostly work in Python and R.
Có thể học thêm: AWS
Not required: coding skills. Nice: BrowserStack""")
    assert result["required_skills"] == ["Python", "R"]
    assert result["preferred_skills"] == ["BrowserStack"]


def test_one_category_reading_for_jobs_and_queries() -> None:
    golang = "Golang Backend Developer\nCông ty tích hợp với Google Cloud.\nYêu cầu: Go, gRPC, PostgreSQL, Redis"
    assert shared.extract_jd_requirements(golang)["category"] == "BACKEND"
    assert shared.detect_for_query(golang) == "BACKEND"
    mobile_qa = "Position: Mobile QA\nYêu cầu: Appium, Postman, Jira, kiểm thử Android và iOS"
    assert shared.extract_jd_requirements(mobile_qa)["category"] == "QA_AUTOMATION"
    both = shared.extract_jd_requirements("DevOps / Cloud Engineer\nTerraform, AWS, Kubernetes")
    assert both["category"] in {"DEVOPS_SRE", "CLOUD"} and {both["category"], both["secondary_category"]} == {"DEVOPS_SRE", "CLOUD"}


def test_title_only_concept_still_counts_for_the_category() -> None:
    result = shared.extract_jd_requirements("Site Reliability Engineer (SRE)\nPython, Go")
    assert "SRE" not in result["required_skills"] and result["category"] == "DEVOPS_SRE"


def test_fingerprint_matches_the_files() -> None:
    fingerprint = shared.catalog_fingerprint()
    assert fingerprint["json_sha256"] == _digest(EMBEDDED / "catalog.json")
    assert fingerprint["py_sha256"] == _digest(EMBEDDED / "catalog.py")


@pytest.mark.parametrize("title, expected", [
    ("Frontend Intern", "FRONTEND"), ("Frontend Data Visualization Developer", "FRONTEND"),
    ("E-commerce Frontend Developer", "FRONTEND"), ("Frontend PWA Developer", "FRONTEND"),
    ("Junior Frontend Accessibility Developer", "FRONTEND"), ("Next.js Frontend Developer", "FRONTEND"),
    ("Front-end Engineer", "FRONTEND"), ("React / Python Full-stack Developer", "FULLSTACK"),
    ("Fullstack Engineer (React, Node.js)", "FULLSTACK"), ("Backend Engineer", "BACKEND"),
    ("Python Backend Developer", "BACKEND"),
])
def test_frontend_is_the_eleventh_category_and_leaves_the_others_alone(title: str, expected: str) -> None:
    assert shared.classify(title, "") == expected
    assert shared.category_by_code()["FRONTEND"].role == "Frontend Developer"


def test_frontend_job_description_routes_to_frontend() -> None:
    jd = "Frontend Developer\nYêu cầu: React, TypeScript, HTML, CSS, Tailwind. Ưu tiên Next.js. 2 năm kinh nghiệm."
    assert shared.extract_jd_requirements(jd)["category"] == "FRONTEND"
    assert shared.detect_for_query("Vue frontend developer, accessibility") == "FRONTEND"
