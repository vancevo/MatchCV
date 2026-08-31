from app.pipeline import analyze_evidence, extract_requirements, screen_candidate


JD = "Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm. Ưu tiên Docker, Redis."


def test_extract_requirements():
    result = extract_requirements(JD)
    assert result["required_skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert result["preferred_skills"] == ["Docker", "Redis"]
    assert result["minimum_experience"] == 2


def test_screen_strong_candidate():
    result = screen_candidate("3 năm Python. Built APIs with FastAPI and PostgreSQL. Docker, Redis.", extract_requirements(JD))
    assert result["final_score"] >= 80
    assert result["recommendation"] == "Strong Match"
    assert len(result["evidence"]) == 5


def test_missing_skill_has_explainable_evidence():
    evidence = analyze_evidence("Python developer", ["Python", "Docker"])
    assert evidence[0]["matched"] is True
    assert evidence[1]["matched"] is False
    assert "Không tìm thấy" in evidence[1]["evidence"]

