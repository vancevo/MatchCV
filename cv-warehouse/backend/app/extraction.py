from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader


CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}

SKILLS = (
    "Python", "Java", "JavaScript", "TypeScript", "React", "Next.js", "Vue", "Angular",
    "Node.js", "FastAPI", "Django", "Spring Boot", ".NET", "C#", "C++", "Go", "Rust",
    "PHP", "Laravel", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes",
    "AWS", "Azure", "GCP", "Terraform", "Jenkins", "GitHub Actions", "Kafka", "Spark",
    "PyTorch", "TensorFlow", "Machine Learning", "NLP", "Computer Vision", "SQL", "Selenium",
)


def extract_text(data: bytes, content_type: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if content_type == "application/pdf" or suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages).strip()
    if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or suffix == ".docx":
        document = Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
    if content_type == "text/plain" or suffix == ".txt":
        return data.decode("utf-8", errors="replace").strip()
    raise ValueError("Chỉ hỗ trợ PDF, DOCX và TXT")


def extract_metadata(text: str, filename: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phone_match = re.search(r"(?<!\d)(?:\+?84|0)[\d .()-]{8,13}(?!\d)", text)
    years = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*(?:\+\s*)?(?:years?|năm)", text, re.I)]
    lowered = text.casefold()
    skills = [skill for skill in SKILLS if skill.casefold() in lowered]
    specialization = "OTHER"
    groups = {
        "AI_ML": ("machine learning", "pytorch", "tensorflow", "nlp", "computer vision", "ai engineer"),
        "DATA": ("data engineer", "data analyst", "spark", "etl", "data warehouse"),
        "DEVOPS": ("devops", "kubernetes", "terraform", "jenkins", "sre"),
        "MOBILE": ("android", "ios", "flutter", "react native"),
        "QA": ("qa engineer", "tester", "selenium", "quality assurance"),
        "FRONTEND": ("frontend", "react", "vue", "angular"),
        "BACKEND": ("backend", "fastapi", "django", "spring boot", "node.js"),
        "FULLSTACK": ("fullstack", "full-stack", "full stack"),
        "SECURITY": ("security engineer", "cybersecurity", "pentest"),
    }
    for name, terms in groups.items():
        if any(term in lowered for term in terms):
            specialization = name
            break
    name = lines[0][:200] if lines else Path(filename).stem
    if "@" in name or len(name.split()) > 8:
        name = Path(filename).stem.replace("_", " ").replace("-", " ")[:200]
    return {
        "full_name": name or "Ứng viên chưa xác định",
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "skills": skills,
        "experience_years": max(years, default=0.0),
        "specialization": specialization,
    }


def chunks(text: str, maximum: int = 900) -> list[str]:
    paragraphs = [value.strip() for value in re.split(r"\n{2,}|(?=^[A-ZĐÁÀẢÃẠĂÂÊÔƠƯ][^\n]{2,50}:?$)", text, flags=re.M) if value.strip()]
    result: list[str] = []
    current = ""
    for paragraph in paragraphs or [text]:
        if current and len(current) + len(paragraph) + 1 > maximum:
            result.append(current)
            current = ""
        if len(paragraph) > maximum:
            for start in range(0, len(paragraph), maximum):
                piece = paragraph[start:start + maximum].strip()
                if piece:
                    result.append(piece)
        else:
            current = f"{current}\n{paragraph}".strip()
    if current:
        result.append(current)
    return result[:100]
