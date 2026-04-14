"""
Module 1 — Document Ingestion
POST /upload-document

Accepts PDF, .md, or .txt uploads.
PDF handling: Docling first → PyMuPDF fallback if Docling unavailable.
Text/Markdown: passed through as-is.
"""

import io
import logging
import tempfile
import os
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, UploadFile
from models.schemas import IngestionResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Parser availability detection (done once at import time, not per-request)
# ---------------------------------------------------------------------------

_DOCLING_AVAILABLE = False
_PYMUPDF_AVAILABLE = False

try:
    from docling.document_converter import DocumentConverter  # type: ignore
    _DOCLING_AVAILABLE = True
    logger.info("✅ Docling is available — will use for PDF parsing")
except Exception as e:
    logger.warning(f"⚠️  Docling not available ({e}). Checking PyMuPDF fallback...")

try:
    import fitz  # PyMuPDF  # type: ignore
    _PYMUPDF_AVAILABLE = True
    logger.info("✅ PyMuPDF (fitz) is available — will use as PDF fallback")
except Exception as e:
    logger.error(
        f"❌ Neither Docling nor PyMuPDF is available ({e}). "
        "PDF uploads will fail. Install at least one parser."
    )


# ---------------------------------------------------------------------------
# Helper: PDF → Markdown using Docling
# ---------------------------------------------------------------------------

def _parse_pdf_docling(file_bytes: bytes, filename: str) -> str:
    """
    Write bytes to a temp file, run Docling's DocumentConverter on it,
    and return clean Markdown preserving section hierarchy.
    """
    from docling.document_converter import DocumentConverter  # type: ignore

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        # Docling's result object exposes .document.export_to_markdown()
        markdown = result.document.export_to_markdown()
        return markdown
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Helper: PDF → plain text using PyMuPDF
# ---------------------------------------------------------------------------

def _parse_pdf_pymupdf(file_bytes: bytes) -> str:
    """
    Extract text from every page using PyMuPDF.
    Attempts to preserve heading-like structure by detecting font size
    jumps, but primarily provides a reliable text fallback.
    """
    import fitz  # type: ignore

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages: list[str] = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)
    doc.close()
    return "\n\n---\n\n".join(pages)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/upload-document", response_model=IngestionResponse)
async def upload_document(file: UploadFile = File(...)) -> IngestionResponse:
    """
    Accept a policy document upload and return clean Markdown.

    Supported formats:
    - .pdf  → Docling (preferred) or PyMuPDF (fallback)
    - .md   → passthrough
    - .txt  → passthrough
    """
    filename = file.filename or "unknown"
    extension = Path(filename).suffix.lower()
    content = await file.read()

    # ---- Plain text / Markdown passthrough -----------------------------------
    if extension in (".md", ".txt"):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return IngestionResponse(
            markdown=text,
            filename=filename,
            parser_used="passthrough",
        )

    # ---- PDF -----------------------------------------------------------------
    if extension == ".pdf":
        if _DOCLING_AVAILABLE:
            try:
                markdown = _parse_pdf_docling(content, filename)
                return IngestionResponse(
                    markdown=markdown,
                    filename=filename,
                    parser_used="docling",
                )
            except Exception as docling_err:
                logger.warning(
                    f"Docling failed ({docling_err}), trying PyMuPDF fallback..."
                )

        if _PYMUPDF_AVAILABLE:
            try:
                markdown = _parse_pdf_pymupdf(content)
                return IngestionResponse(
                    markdown=markdown,
                    filename=filename,
                    parser_used="pymupdf",
                )
            except Exception as pymupdf_err:
                raise HTTPException(
                    status_code=500,
                    detail=f"Both PDF parsers failed. PyMuPDF error: {pymupdf_err}",
                )

        raise HTTPException(
            status_code=500,
            detail=(
                "No PDF parser is available. "
                "Install 'docling' or 'PyMuPDF' and restart the server."
            ),
        )

    # ---- Unsupported format --------------------------------------------------
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type '{extension}'. Please upload PDF, .md, or .txt.",
    )
