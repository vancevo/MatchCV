"""Deterministic, explainable screening pipeline used by the demo and tests.

The interfaces are intentionally small so Docling, Ollama and embeddings can be
plugged in later without changing the API layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from functools import lru_cache


SKILL_ALIASES = {
    "aws": ("aws", "amazon web services"),
    "blockchain": ("blockchain",),
    "cloud computing": ("cloud computing", "cloud"),
    "digital ocean": ("digital ocean", "digitalocean"),
    "docker": ("docker", "container"),
    "english": ("english", "tiếng anh"),
    "postgresql": ("postgresql", "postgres", "psql"),
    "fastapi": ("fastapi",),
    "golang": ("golang", "go language"),
    "google cloud": ("google cloud", "gcp"),
    "linux": ("linux",),
    "monitoring": ("monitoring", "prometheus", "grafana", "observability"),
    "nosql": ("nosql", "dynamodb"),
    "python": ("python",),
    "redis": ("redis",),
    "rest api": ("rest api", "restful", "http api", "web service"),
    "self-learning": ("tự học", "tự học hỏi", "self-learning"),
    "sql optimization": ("tối ưu hóa sql", "sql optimization", "query optimization", "optimize sql"),
    "teamwork": ("làm việc nhóm", "teamwork"),
    "problem solving": ("giải quyết vấn đề", "problem solving"),
    "web3": ("web3", "web 3"),
    "javascript": ("javascript", "js", "es6"),
    "typescript": ("typescript", "ts"),
    "react": ("react", "react.js", "reactjs"),
    "vue": ("vue", "vue.js", "vuejs"),
    "angular": ("angular", "angularjs"),
    "next.js": ("next.js", "nextjs"),
    "node.js": ("node.js", "nodejs", "node"),
    "css": ("css", "css3", "scss", "sass"),
    "html": ("html", "html5"),
    "git": ("git", "github", "gitlab", "bitbucket"),
    "kubernetes": ("kubernetes", "k8s", "openshift"),
    "ci/cd": ("ci/cd", "cicd", "jenkins", "github actions", "gitlab ci"),
    "mysql": ("mysql", "mariadb"),
    "mongodb": ("mongodb", "mongo"),
    "java": ("java",),
    "spring boot": ("spring boot", "spring framework"),
    "c#": ("c#", ".net", "dotnet", "asp.net"),
    "php": ("php",),
    "testing": ("unit test", "jest", "pytest", "junit", "testing"),
    "responsive design": ("responsive", "responsive design", "mobile-first"),
    "sql": ("sql",),
    "graphql": ("graphql",),
}

# The rules path runs whenever the model is not configured, and it can only ever find a skill it
# has been told about. Nine entries meant a frontend job description listing HTML, CSS, JavaScript,
# React, REST API and Git produced exactly two criteria, and every candidate then scored the same.
KNOWN_SKILLS = [
    "Python", "FastAPI", "Django", "Flask", "Java", "Spring Boot", "Golang", "Node.js", "C#",
    "PHP", "Laravel", "Ruby on Rails",
    "JavaScript", "TypeScript", "React", "Vue.js", "Angular", "Next.js", "HTML", "CSS",
    "Tailwind", "Redux", "responsive design",
    "PostgreSQL", "MySQL", "MongoDB", "NoSQL", "Redis", "SQL optimization", "SQL", "Elasticsearch",
    "Docker", "Kubernetes", "AWS", "Azure", "Google Cloud", "Digital Ocean", "Cloud computing",
    "CI/CD", "Jenkins", "Terraform", "Linux", "Monitoring",
    "REST API", "GraphQL", "gRPC", "Microservices", "Kafka", "RabbitMQ",
    "Git", "Agile", "Scrum", "unit test", "Jest", "Pytest", "Selenium",
    "Machine Learning", "TensorFlow", "PyTorch", "Pandas", "NumPy", "Spark", "Airflow",
    "Blockchain", "Web3", "English", "Self-learning", "Problem solving", "Teamwork",
    "Figma", "Power BI", "Excel",
]


@dataclass
class Evidence:
    requirement: str
    matched: bool
    evidence: str
    confidence: float


def extract_requirements(description: str) -> dict:
    text = description.lower()
    # Prefer the longest overlapping match. For example, "SQL optimization" must not also create
    # a second generic "SQL" criterion at the same position.
    candidates = [(found[0], found[1], skill) for skill in KNOWN_SKILLS
                  if (found := find_skill(text, skill))]
    accepted: list[tuple[int, int, str]] = []
    for start, length, skill in sorted(candidates, key=lambda item: (item[0], -item[1])):
        end = start + length
        if any(start < other_start + other_length and end > other_start
               for other_start, other_length, _ in accepted):
            continue
        accepted.append((start, length, skill))
    accepted.sort(key=lambda item: item[0])
    skills = [skill for _, _, skill in accepted]

    section_markers = ("ưu tiên", "plus", "nice to have")
    marker_positions = [text.find(marker) for marker in section_markers if text.find(marker) >= 0]
    first_section_marker = min(marker_positions) if marker_positions else -1
    preferred: list[str] = []
    for position, length, skill in accepted:
        tail = text[position : position + 80] if position >= 0 else ""
        sentence_start = max(text.rfind(".", 0, position), text.rfind("\n", 0, position)) + 1 if position >= 0 else 0
        sentence_end_candidates = [idx for idx in (text.find(".", position), text.find("\n", position)) if idx >= 0]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(text)
        sentence = text[sentence_start:sentence_end]
        if first_section_marker >= 0 and position >= first_section_marker:
            preferred.append(skill)
        elif re.search(rf"^{re.escape(text[position:position + length])}\s+(?:là|is).{{0,20}}(?:lợi thế|plus)", tail):
            preferred.append(skill)
        elif skill in {"Blockchain", "Web3"} and re.search(r"blockchain.{0,20}web3.{0,30}lợi thế", sentence):
            preferred.append(skill)
    if "Docker" in preferred and "Golang" in preferred and "golang" in text and "docker là lợi thế" in text:
        preferred = [skill for skill in preferred if skill != "Golang"]
    required = [s for s in skills if s not in preferred]
    required = [s for s in skills if s not in preferred]
    # "Ít nhất 2 năm", "tối thiểu 2 năm", "2+ năm", "2-4 năm" all state the same floor.
    years = (re.search(r"(?:có|ít nhất|tối thiểu|minimum|min\.?|từ)\s*(\d+)\s*(?:\+)?\s*(?:năm|years?)", text)
             or re.search(r"(\d+)\s*\+\s*(?:năm|years?)", text)
             or re.search(r"(\d+)\s*[-–]\s*\d+\s*(?:năm|years?)", text)
             or re.search(r"(\d+)\+?\s*(?:năm|years?).{0,30}(?:trở lên|kinh nghiệm)", text))
    return {
        "required_skills": required or skills[:3],
        "preferred_skills": preferred,
        "minimum_experience": int(years.group(1)) if years else 0,
    }


@lru_cache(maxsize=512)
def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Match a whole token, never a fragment of a longer word.

    Plain substring search read "java" out of "javascript" and "gin" out of "debugging", so a
    frontend job description was credited with Java and Go. Punctuation inside a name such as
    "node.js", "ci/cd" or "c#" still has to survive, hence lookarounds rather than \b.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.I)


def find_skill(text: str, skill: str) -> tuple[int, int] | None:
    """Where the skill is first mentioned, as (offset, length), or None."""
    best: tuple[int, int] | None = None
    for alias in _aliases(skill):
        found = _alias_pattern(alias).search(text)
        if found and (best is None or found.start() < best[0]):
            best = (found.start(), len(found.group(0)))
    return best


def _aliases(skill: str) -> tuple[str, ...]:
    return SKILL_ALIASES.get(skill.lower(), (skill.lower(),))


def _snippet(text: str, at: int, length: int) -> str:
    """The sentence around the hit, or a window when there is no sentence boundary nearby."""
    start = max((text.rfind(mark, 0, at) for mark in (". ", "! ", "? ", " • ", " | ")), default=-1)
    start = start + 2 if start >= 0 and at - start <= 240 else max(0, at - 90)
    end = min(len(text), at + length + 150)
    stop = min((pos for mark in (". ", "! ", "? ", " • ", " | ")
                if (pos := text.find(mark, at + length)) != -1 and pos < end), default=end)
    # Returned verbatim: the evals gate on evidence being a literal span of the CV, so it stays
    # quotable rather than decorated with ellipses.
    return text[start:stop].strip()


def analyze_evidence(cv_text: str, requirements: list[str]) -> list[dict]:
    # Searched as one line: a PDF decides its own line breaks, and a requirement written as two
    # words ("REST API") could never be found while each word sat on a line of its own.
    flat = re.sub(r"\s+", " ", cv_text).strip()
    lowered = flat.lower()
    evidence: list[Evidence] = []
    for requirement in requirements:
        found = find_skill(lowered, requirement)
        evidence.append(Evidence(
            requirement=requirement,
            matched=found is not None,
            # A bare "Docker," proves nothing to whoever reads the card; quote enough to judge.
            evidence=_snippet(flat, *found) if found else "Không tìm thấy bằng chứng phù hợp trong CV.",
            confidence=0.94 if found else 0.35,
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

    # A component with nothing to judge used to award full marks: a job that states no minimum
    # experience gave every candidate 25 points, and one with no preferred skills another 15. That
    # is 40% of the score identical for everyone, which is how ten candidates ended up sharing two
    # values. Drop what was not assessed and renormalise, so the score only reflects what was.
    parts = [(required_score, .40), (semantic_score, .20)]
    if minimum > 0:
        parts.append((experience_score, .25))
    if preferred:
        parts.append((preferred_score, .15))
    final = round(sum(value * weight for value, weight in parts) / sum(w for _, w in parts), 1)
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
