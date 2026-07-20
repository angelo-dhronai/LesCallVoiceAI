"""
Extract plain text from uploaded documents.
Supports: PDF (via pdfplumber, with Gemini-vision OCR fallback for scanned
pages), images (Gemini-vision OCR), DOCX (via python-docx), TXT/CSV/MD (plain).

OCR strategy (production): the embedded text layer is always tried first —
it is free, instant and exact. A PDF is treated as scanned when it yields
almost no text per page; only then is the raw file sent to Gemini
(multimodal) for a verbatim transcription. No system OCR deps (tesseract,
poppler) are required, which keeps the container image unchanged.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}
# Below this many chars per page on average, a PDF is considered scanned.
_SCANNED_PDF_CHARS_PER_PAGE = 200
# Gemini inline_data caps the whole request at ~20 MB; leave headroom.
_OCR_MAX_BYTES = 14 * 1024 * 1024


def extract_text(content: bytes, filename: str) -> str:
    """Return plain text from raw file bytes. filename used only for extension detection."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(content)
        elif ext in (".docx", ".doc"):
            return _extract_docx(content)
        elif ext in _IMAGE_EXTS:
            return _ocr_via_gemini(content, _IMAGE_EXTS[ext])
        else:
            return content.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.error("[docs/extract] Failed to extract %s: %s", filename, exc)
        raise ValueError(f"Could not extract text from {filename}: {exc}") from exc


def _extract_pdf(content: bytes) -> str:
    import pdfplumber
    parts: list[str] = []
    n_pages = 0
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            n_pages += 1
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
    text = "\n\n".join(parts)
    if n_pages and len(text) / n_pages >= _SCANNED_PDF_CHARS_PER_PAGE:
        return text
    # Sparse or empty text layer — likely a scanned/image PDF. OCR it and
    # keep whichever result carries more content.
    try:
        ocr = _ocr_via_gemini(content, "application/pdf")
    except Exception as exc:
        logger.warning("[docs/extract] OCR fallback failed (%s) — using text layer "
                       "(%d chars)", exc, len(text))
        return text
    return ocr if len(ocr) > len(text) else text


def _extract_docx(content: bytes) -> str:
    import docx as _docx
    doc = _docx.Document(io.BytesIO(content))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paras)


def _ocr_via_gemini(content: bytes, mime_type: str) -> str:
    """Verbatim transcription of a scanned PDF / image via Gemini multimodal.

    Uses the native generateContent API (the OpenAI-compat endpoint does not
    take PDF inline data). Key comes from GEMINI_API_KEY / LLM_API_KEY; model
    is overridable via DOC_OCR_MODEL.
    """
    if len(content) > _OCR_MAX_BYTES:
        raise ValueError(
            f"file too large for OCR ({len(content) // (1024 * 1024)} MB > "
            f"{_OCR_MAX_BYTES // (1024 * 1024)} MB) and it has no text layer")
    api_key = (os.environ.get("GEMINI_API_KEY", "").strip()
               or os.environ.get("LLM_API_KEY", "").strip())
    if not api_key:
        raise ValueError("no GEMINI_API_KEY/LLM_API_KEY configured for OCR")
    model = os.environ.get("DOC_OCR_MODEL", "").strip() or "gemini-3.1-flash-lite"
    import httpx
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [
                {"inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(content).decode("ascii"),
                }},
                {"text": (
                    "Transcribe ALL text content of this document verbatim, in "
                    "reading order, as plain markdown. Preserve headings, lists "
                    "and tables. Do not summarize, translate, or add commentary."
                )},
            ]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 65536},
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
    logger.info("[docs/extract] Gemini OCR (%s, %d bytes) -> %d chars",
                mime_type, len(content), len(text))
    return text
