"""
Extract plain text from uploaded documents.
Supports: PDF (via pdfplumber), DOCX (via python-docx), TXT/CSV/MD (plain).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text(content: bytes, filename: str) -> str:
    """Return plain text from raw file bytes. filename used only for extension detection."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(content)
        elif ext in (".docx", ".doc"):
            return _extract_docx(content)
        else:
            return content.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.error("[docs/extract] Failed to extract %s: %s", filename, exc)
        raise ValueError(f"Could not extract text from {filename}: {exc}") from exc


def _extract_pdf(content: bytes) -> str:
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts)


def _extract_docx(content: bytes) -> str:
    import docx as _docx
    doc = _docx.Document(io.BytesIO(content))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paras)
