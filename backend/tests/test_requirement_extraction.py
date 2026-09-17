"""The rules path runs whenever no model is configured, so it decides what a job is judged on."""
from app.pipeline import extract_requirements, find_skill, screen_candidate


FRONTEND_JD = """Tham gia phát triển giao diện web dưới sự hướng dẫn của Senior Developer.

Yêu cầu:
- 0–2 năm kinh nghiệm phát triển frontend.
- Biết HTML, CSS và JavaScript.
- Có kinh nghiệm với React.js hoặc Vue.js.
- Hiểu REST API và cách tích hợp API vào giao diện.
- Biết Git và quy trình làm việc với GitHub/GitLab.
- Có kiến thức cơ bản về responsive design và debugging.
"""


def test_a_frontend_job_yields_more_than_two_criteria():
    required = extract_requirements(FRONTEND_JD)["required_skills"]
    # The old nine-word vocabulary saw only REST API and React here, so every frontend candidate
    # answered the same two questions and scored identically.
    for skill in ("HTML", "CSS", "JavaScript", "React", "Git", "REST API"):
        assert skill in required, f"thiếu {skill}: {required}"


def test_a_skill_is_not_read_out_of_a_longer_word():
    required = extract_requirements(FRONTEND_JD)["required_skills"]
    # "java" sits inside "javascript" and "gin" inside "debugging".
    assert "Java" not in required
    assert "Golang" not in required


def test_criteria_follow_the_order_of_the_job_description():
    jd = "Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm. Ưu tiên Docker, Redis."
    result = extract_requirements(jd)
    assert result["required_skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert result["preferred_skills"] == ["Docker", "Redis"]


def test_minimum_experience_is_read_from_several_phrasings():
    for jd, expected in [
        ("Ít nhất 2 năm kinh nghiệm với Python.", 2),
        ("Tối thiểu 4 năm kinh nghiệm Python.", 4),
        ("Cần 3+ năm kinh nghiệm Python.", 3),
        ("Yêu cầu 2-4 năm kinh nghiệm Python.", 2),
        ("Không yêu cầu kinh nghiệm, biết Python là được.", 0),
    ]:
        assert extract_requirements(jd)["minimum_experience"] == expected, jd


def test_find_skill_respects_token_boundaries():
    assert find_skill("dùng node.js và ci/cd hằng ngày", "Node.js")
    assert find_skill("lập trình c# trên .net", "C#")
    assert find_skill("kinh nghiệm javascript", "JavaScript")
    assert find_skill("kinh nghiệm javascript", "Java") is None
    assert find_skill("kỹ năng debugging tốt", "Golang") is None


def test_aliases_do_not_create_duplicate_or_wrong_criteria():
    assert extract_requirements("Yêu cầu GraphQL API")["required_skills"] == ["GraphQL"]
    assert extract_requirements("Yêu cầu Tailwind")["required_skills"] == ["Tailwind"]


def test_preferred_skill_can_be_written_as_an_alias():
    result = extract_requirements("Python bắt buộc. Ưu tiên JS")
    assert result["required_skills"] == ["Python"]
    assert result["preferred_skills"] == ["JavaScript"]


def test_richer_criteria_separate_candidates_that_used_to_tie():
    strong = "React TypeScript JavaScript HTML CSS Redux Tailwind Jest Git responsive design"
    weak = "React JavaScript CSS Git"
    criteria = extract_requirements(FRONTEND_JD)
    # Same engine, same CVs: what changed is how many questions the job actually asks.
    assert screen_candidate(strong, criteria)["final_score"] > screen_candidate(weak, criteria)["final_score"]
