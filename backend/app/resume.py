from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi import HTTPException, UploadFile
from pypdf import PdfReader


ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}
MAX_FILE_SIZE = 10 * 1024 * 1024


async def extract_resume(file: UploadFile) -> tuple[bytes, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"{file.filename}: chỉ hỗ trợ PDF, DOCX và TXT")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, f"{file.filename}: file vượt quá 10 MB")
    try:
        if suffix == ".pdf":
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        elif suffix == ".docx":
            text = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
        else:
            text = content.decode("utf-8-sig")
    except Exception as exc:
        raise HTTPException(422, f"{file.filename}: không đọc được nội dung") from exc
    text = text.strip()
    if len(text) < 20:
        raise HTTPException(422, f"{file.filename}: không có đủ text để phân tích")
    return content, text


def candidate_identity(text: str, filename: str | None) -> tuple[str, str]:
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I)
    email = email_match.group(0) if email_match else ""
    lines = [re.sub(r"\s+", " ", line).strip(" |—-") for line in text.splitlines() if line.strip()]
    name = next((line for line in lines[:8] if 2 <= len(line) <= 80 and "@" not in line and not re.search(r"\d", line)), "")
    if not name:
        name = Path(filename or "Ứng viên chưa xác định").stem.replace("_", " ").replace("-", " ").strip()
    return name[:200], email[:320]


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
