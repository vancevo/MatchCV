"""A PDF decides where its own lines break; nothing downstream may depend on that choice."""
from app.pipeline import analyze_evidence
from app.resume import candidate_identity, flatten


CV = (
    "NGUYỄN THỊ HỒNG NHUNG\n"
    "Senior Backend Engineer (Go, Kubernetes) | 8 năm kinh nghiệm\n"
    "Điện thoại:0917 620 483 • Email: hong.nhung@example.com\n"
    "Xây dựng REST API bằng Python và FastAPI cho hệ thống nội bộ.\n"
    "Triển khai Machine Learning trên AWS, dùng Docker và PostgreSQL.\n"
)
ONE_WORD_PER_LINE = "\n".join(CV.split())


def test_full_name_survives_instead_of_only_the_surname():
    name, email = candidate_identity(CV, "cv.pdf")
    # pypdf used to hand us one word per line, so this was cut down to "NGUYỄN".
    assert name == "NGUYỄN THỊ HỒNG NHUNG"
    assert email == "hong.nhung@example.com"


def test_name_is_read_the_same_however_the_pdf_broke_its_lines():
    assert candidate_identity(ONE_WORD_PER_LINE, "cv.pdf")[0] == "NGUYỄN THỊ HỒNG NHUNG"


def test_a_job_title_line_is_not_mistaken_for_a_name():
    text = "CURRICULUM VITAE\nSenior Backend Engineer\nTRẦN VĂN MINH\ntran.minh@example.com"
    assert candidate_identity(text, "cv.pdf")[0] == "TRẦN VĂN MINH"


def test_filename_is_still_the_last_resort():
    assert candidate_identity("2024 2025 2026\n0908573214", "Ho_So-Ung_Vien.pdf")[0] == "Ho So Ung Vien"


def test_multi_word_requirements_match_whatever_the_line_breaks_are():
    wanted = ["REST API", "Machine Learning", "Python"]
    for text in (CV, ONE_WORD_PER_LINE):
        found = {item["requirement"] for item in analyze_evidence(text, wanted) if item["matched"]}
        # Line-by-line matching could never see a two-word phrase split across two lines.
        assert found == set(wanted), f"thiếu: {set(wanted) - found}"


def test_evidence_quotes_a_readable_span_not_a_single_word():
    quote = next(item for item in analyze_evidence(CV, ["Docker"]) if item["matched"])["evidence"]
    # The old version handed the recruiter "Docker," on its own, which proves nothing.
    assert len(quote.split()) >= 6
    assert "PostgreSQL" in quote


def test_a_requirement_absent_from_the_cv_is_still_reported_missing():
    result = analyze_evidence(CV, ["Elixir"])[0]
    assert result["matched"] is False
    assert result["confidence"] < 0.5


def test_flatten_makes_one_searchable_line():
    assert "\n" not in flatten(CV)
    assert "REST API" in flatten(ONE_WORD_PER_LINE)
