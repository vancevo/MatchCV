from __future__ import annotations

import hashlib
import itertools
import re
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi import HTTPException, UploadFile
from pypdf import PdfReader

from .config import get_settings


ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}
async def extract_resume(file: UploadFile) -> tuple[bytes, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"{file.filename}: chỉ hỗ trợ PDF, DOCX và TXT")
    content = await file.read()
    max_file_size = get_settings().max_upload_mb * 1024 * 1024
    if len(content) > max_file_size:
        raise HTTPException(413, f"{file.filename}: file vượt quá {get_settings().max_upload_mb} MB")
    try:
        if suffix == ".pdf":
            text = _pdf_text(content)
        elif suffix == ".docx":
            text = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
        else:
            text = content.decode("utf-8-sig")
    except Exception as exc:
        raise HTTPException(422, f"{file.filename}: không đọc được nội dung") from exc
    text = _normalise(text)
    if len(text) < 20:
        raise HTTPException(422, f"{file.filename}: không có đủ text để phân tích")
    return content, text


def _pdf_text(content: bytes) -> str:
    """Read the page as it is laid out, not as a stream of glyph runs.

    pypdf's default mode emits one word per line for these CVs, which quietly breaks three
    things downstream: a name is cut to its first word, no multi-word requirement can ever be
    found, and every evidence quote shrinks to a single word. Layout mode keeps real lines; the
    plain mode is only the fallback for a page it cannot lay out.
    """
    pages = []
    for page in PdfReader(BytesIO(content)).pages:
        try:
            rendered = page.extract_text(extraction_mode="layout") or ""
        except Exception:
            rendered = ""
        if len(rendered.split()) < 3:
            rendered = page.extract_text() or ""
        pages.append(rendered)
    return "\n".join(pages)


def _normalise(text: str) -> str:
    """Collapse the padding layout mode leaves behind, while keeping the line breaks."""
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def flatten(text: str) -> str:
    """One searchable line. Matching a phrase must not depend on where the PDF broke a line."""
    return re.sub(r"\s+", " ", text)


# A name line is written in capitals on these CVs, but a lowercase one still has to work, so the
# shape is what is tested: a few words, no digits, no punctuation that belongs to a contact line.
_NAME_STOPWORDS = re.compile(
    r"\b(cv|curriculum|vitae|resume|h[oồ] s[oơ]|ứng viên|engineer|developer|"
    r"intern|manager|senior|junior|lead)\b", re.I)


def _looks_like_name(line: str) -> bool:
    if not (2 <= len(line) <= 60) or re.search(r"[\d@:•|/\\]", line):
        return False
    words = line.split()
    if not (2 <= len(words) <= 6):
        return False
    if _NAME_STOPWORDS.search(line):
        return False
    return all(word[0].isalpha() for word in words)


def _name_from_split_lines(lines: list[str]) -> str:
    """Rebuild a name that a badly laid out PDF scattered one word to a line.

    Layout mode fixes this at the source, but an unusual PDF can still come through word by
    word, and a candidate card showing only a surname is worse than a slightly greedy guess.
    """
    if sum(1 for line in lines[:12] if " " in line) > 2:
        return ""  # Real lines here, so nothing was scattered.
    run = list(itertools.takewhile(
        lambda line: line.isalpha() and 1 < len(line) <= 20, lines[:6]))
    # Longest first: the run runs past the name into the job title underneath it.
    for size in range(min(5, len(run)), 1, -1):
        joined = " ".join(run[:size])
        if _looks_like_name(joined):
            return joined
    return ""


def candidate_identity(text: str, filename: str | None) -> tuple[str, str]:
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I)
    email = email_match.group(0) if email_match else ""
    lines = [re.sub(r"\s+", " ", line).strip(" |—-·•") for line in text.splitlines() if line.strip()]
    name = next((line for line in lines[:8] if _looks_like_name(line)), "")
    if not name:
        name = _name_from_split_lines(lines)
    if not name:
        # Nothing name-shaped up top: keep the old rule rather than leave the card blank.
        name = next((line for line in lines[:8]
                     if 2 <= len(line) <= 80 and "@" not in line and not re.search(r"\d", line)), "")
    if not name:
        name = Path(filename or "Ứng viên chưa xác định").stem.replace("_", " ").replace("-", " ").strip()
    return name[:200], email[:320]


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
