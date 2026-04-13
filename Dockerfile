# ============================================================
# PolicyPilot — Hugging Face Spaces Docker Image
# Base: python:3.10-slim (Debian Bullseye)
# Port: 7860 (mandatory for HF Spaces)
# ============================================================

# Python 3.10 matches the local dev environment (locks away
# any silent 3.11 incompatibilities in Docling / PyTorch deps)
FROM python:3.10-slim

# ── System packages ─────────────────────────────────────────
# Docling needs:
#   - libgl1 + libglib2.0-0 : OpenCV (used by docling's vision pipeline)
#   - libgomp1              : OpenMP runtime (PyTorch parallel ops)
#   - poppler-utils         : pdfinfo / pdftotext fallback
#   - tesseract-ocr         : OCR fallback when Docling model is unavailable
#   - curl                  : health-check / debugging utility
# build-essential is NOT needed — we only install pre-built wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user (required by Hugging Face Spaces) ─────────
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    # Silence HuggingFace symlink warnings on read-only FS
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    # Keep Python output unbuffered so logs appear in real time
    PYTHONUNBUFFERED=1

# ── Working directory ────────────────────────────────────────
WORKDIR $HOME/app

# ── Install dependencies (layered for cache efficiency) ─────
# Copy ONLY the requirement files first — Docker will cache
# this layer and skip re-installing packages on every code push.
COPY --chown=user backend/requirements.txt ./requirements.txt
COPY --chown=user backend/requirements-docling.txt ./requirements-docling.txt

# Core dependencies first (fast), then Docling (slow, ~2 GB)
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r requirements-docling.txt

# ── Copy application source ──────────────────────────────────
# Done AFTER pip install so code changes don't bust the cache
COPY --chown=user backend/ ./

# ── Port ─────────────────────────────────────────────────────
EXPOSE 7860

# ── Boot ─────────────────────────────────────────────────────
# --workers 1  : single worker avoids in-memory state sharding
#                (active_ruleset is stored in Python module state)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
