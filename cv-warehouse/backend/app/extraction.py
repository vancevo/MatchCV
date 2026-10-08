from __future__ import annotations

import io
import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .catalog import catalog as shared


CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


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


NON_NAME_LINES = {
    "curriculum vitae", "resume", "cv", "profile", "summary", "contact", "skills", "experience",
    "education", "technical skills", "hồ sơ", "lý lịch", "thông tin cá nhân",
}


def guess_name(lines: list[str], filename: str) -> str:
    """Pick the candidate name from the header, skipping avatar initials, headings and contact lines."""
    for line in lines[:8]:
        words = line.split()
        if not 2 <= len(words) <= 6 or len(line) > 60:
            continue
        if line.casefold() in NON_NAME_LINES or re.search(r"[@\d:/|]", line):
            continue
        if all(word[:1].isupper() and word.replace("'", "").replace("-", "").replace(".", "").isalpha() for word in words):
            return line
    return Path(filename).stem.replace("_", " ").replace("-", " ")[:200]


def guess_title(lines: list[str], name: str) -> str:
    """The headline right under the candidate name (e.g. "QA Intern"), or "" when there is none."""
    if name not in lines:
        return ""
    for line in lines[lines.index(name) + 1:][:2]:
        if len(line) <= 80 and not re.search(r"[@|:]|https?", line):
            return line
    return ""


def extract_metadata(text: str, filename: str) -> dict:
    """Header fields plus everything the shared catalog can read from the CV (skills, category, level...)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phone_match = re.search(r"(?<!\d)(?:\+?84|0)[\d .()-]{8,13}(?!\d)", text)
    years = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*(?:\+\s*)?(?:years?|năm)", text, re.I)]
    name = guess_name(lines, filename)
    job_title = guess_title(lines, name)
    profile = shared.extract_cv_profile(text, job_title)
    return {
        "full_name": name or "Ứng viên chưa xác định",
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "job_title": job_title,
        "skills": profile["skills"],
        "experience_years": max(years, default=0.0),
        "specialization": profile["category"],
        "education_level": profile["education_level"],
        "languages": profile["languages"],
        "level": profile["level"] or "",
        "certifications": profile["certifications"],
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
