"""Deterministic, explainable screening pipeline used by the demo and tests.

The interfaces are intentionally small so Docling, Ollama and embeddings can be
plugged in later without changing the API layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict


SKILL_ALIASES = {
    "postgresql": ("postgresql", "postgres", "psql"),
    "fastapi": ("fastapi",),
    "python": ("python",),
    "docker": ("docker", "container"),
    "redis": ("redis",),
    "rest api": ("rest api", "restful", "http api", "web service"),
}


@dataclass
class Evidence:
    requirement: str
    matched: bool
    evidence: str
    confidence: float


def extract_requirements(description: str) -> dict:
    text = description.lower()
    known = ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "REST API", "React", "TypeScript", "AWS"]
    skills = [skill for skill in known if skill.lower() in text]
    preferred_marker = text.find("ưu tiên")
    preferred = [s for s in skills if preferred_marker >= 0 and text.find(s.lower()) > preferred_marker]
    required = [s for s in skills if s not in preferred]
    years = re.search(r"(?:ít nhất|minimum|min\.?)\s*(\d+)\s*(?:năm|years?)", text)
    return {
        "required_skills": required or skills[:3],
        "preferred_skills": preferred,
        "minimum_experience": int(years.group(1)) if years else 0,
    }


def _aliases(skill: str) -> tuple[str, ...]:
    return SKILL_ALIASES.get(skill.lower(), (skill.lower(),))


def analyze_evidence(cv_text: str, requirements: list[str]) -> list[dict]:
    lines = [line.strip() for line in cv_text.splitlines() if line.strip()]
    lowered = [line.lower() for line in lines]
    evidence: list[Evidence] = []
    for requirement in requirements:
        aliases = _aliases(requirement)
        hit = next((lines[i] for i, line in enumerate(lowered) if any(a in line for a in aliases)), None)
        evidence.append(Evidence(
            requirement=requirement,
            matched=hit is not None,
            evidence=hit or "Không tìm thấy bằng chứng phù hợp trong CV.",
            confidence=0.94 if hit else 0.35,
        ))
    return [asdict(item) for item in evidence]


def screen_candidate(cv_text: str, requirements: dict) -> dict:
    required = requirements.get("required_skills", [])
    preferred = requirements.get("preferred_skills", [])
    all_evidence = analyze_evidence(cv_text, required + preferred)
    required_hits = sum(x["matched"] for x in all_evidence[: len(required)])
    preferred_hits = sum(x["matched"] for x in all_evidence[len(required) :])
    required_score = 100 * required_hits / max(len(required), 1)
    preferred_score = 100 * preferred_hits / max(len(preferred), 1) if preferred else 100

    year_values = [int(v) for v in re.findall(r"(\d+)\+?\s*(?:năm|years?)", cv_text.lower())]
    years = max(year_values, default=0)
    minimum = requirements.get("minimum_experience", 0)
    experience_score = 100 if minimum == 0 else min(100, years / minimum * 100)

    # Lightweight lexical semantic proxy for the offline MVP.
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z+#.]{1,}", cv_text.lower()))
    semantic_hits = sum(any(alias in cv_text.lower() for alias in _aliases(skill)) for skill in required)
    semantic_score = min(100, 35 + semantic_hits * 65 / max(len(required), 1) + min(len(tokens), 80) / 8)
    final = round(required_score * .40 + experience_score * .25 + semantic_score * .20 + preferred_score * .15, 1)
    recommendation = "Strong Match" if final >= 80 else "Potential Match" if final >= 65 else "Needs Review"
    return {
        "rule_score": round(required_score, 1),
        "experience_score": round(experience_score, 1),
        "semantic_score": round(semantic_score, 1),
        "preferred_score": round(preferred_score, 1),
        "final_score": final,
        "recommendation": recommendation,
        "evidence": all_evidence,
        "experience_years": years,
    }


def generate_interview_kit(cv_text: str, requirements: dict, screening: dict, job_title: str = "") -> dict:
    """Create a deterministic interview kit when an LLM is unavailable."""
    evidence = screening.get("evidence", [])
    matched = [item["requirement"] for item in evidence if item.get("matched")]
    missing = [item["requirement"] for item in evidence if not item.get("matched")]
    focus = matched[:3] or requirements.get("required_skills", [])[:3] or ["kinh nghiệm phù hợp"]
    gap = missing[0] if missing else ""
    title = job_title or "vị trí đang tuyển"
    gap_question = (
        f"CV chưa thể hiện rõ {gap}. Bạn đã từng dùng hoặc học phần này trong bối cảnh nào?"
        if gap else
        "Trong các kỹ năng đã nêu ở CV, phần nào bạn tự tin nhất và phần nào vẫn muốn phát triển thêm?"
    )
    gap_signal = (
        "Ứng viên phân biệt được kinh nghiệm thật, mức độ tự học và khả năng tiếp thu."
        if gap else
        "Ứng viên tự đánh giá thực tế, biết điểm mạnh và kế hoạch phát triển tiếp theo."
    )
    questions = [
        {
            "type": "CV verification",
            "question": f"Bạn hãy mô tả dự án gần nhất thể hiện rõ kinh nghiệm {focus[0]} cho {title}.",
            "signal": "Ứng viên nêu được vai trò cá nhân, phạm vi công việc, kết quả và bằng chứng cụ thể.",
        },
        {
            "type": "Technical depth",
            "question": f"Nếu phải thiết kế một tính năng production dùng {focus[-1]}, bạn sẽ xử lý lỗi, logging và kiểm thử như thế nào?",
            "signal": "Câu trả lời có trade-off kỹ thuật, cách kiểm chứng và hiểu biết vận hành.",
        },
        {
            "type": "Gap probing",
            "question": gap_question,
            "signal": gap_signal,
        },
        {
            "type": "Scenario",
            "question": "Khi yêu cầu thay đổi sát deadline và có rủi ro ảnh hưởng chất lượng, bạn sẽ trao đổi với team như thế nào?",
            "signal": "Tư duy ưu tiên, giao tiếp minh bạch và biết bảo vệ chất lượng sản phẩm.",
        },
        {
            "type": "Decision support",
            "question": "Sau buổi phỏng vấn này, điểm mạnh nào của bạn khiến team nên chọn bạn cho vị trí này?",
            "signal": "Ứng viên tự liên hệ năng lực với JD, không trả lời chung chung.",
        },
    ]
    return {
        "summary": f"Bộ câu hỏi tập trung kiểm chứng evidence trong CV và làm rõ các khoảng thiếu so với {title}.",
        "questions": questions,
        "rubric": [
            {"criterion": "Độ khớp với yêu cầu bắt buộc", "weight": 35},
            {"criterion": "Chiều sâu kinh nghiệm thực tế", "weight": 25},
            {"criterion": "Tư duy giải quyết vấn đề", "weight": 20},
            {"criterion": "Giao tiếp và phối hợp", "weight": 10},
            {"criterion": "Khả năng học phần còn thiếu", "weight": 10},
        ],
        "source": "rules",
    }
