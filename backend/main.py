"""
PolicyPilot — FastAPI Application Entrypoint
"""

import logging
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# Configure logging before importing modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from modules.ingestion import router as ingestion_router
from modules.extraction import router as extraction_router
from modules.finalization import router as finalization_router
from modules.rule_engine import router as rule_engine_router
from modules.doc_extraction import router as doc_extraction_router
from modules.reporting import router as reporting_router
from modules.rule_graph import router as rule_graph_router

app = FastAPI(
    title="PolicyPilot",
    description="AP Policy Rule Extraction and Execution System",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS — allow frontend dev server
# ---------------------------------------------------------------------------

frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Fixed for Live Website Deployment (Accepts requests from any Vercel URL)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(ingestion_router, tags=["Module 1 — Ingestion"])
app.include_router(extraction_router, tags=["Module 2 — Extraction"])
app.include_router(finalization_router, tags=["Module 3 — Finalization"])
app.include_router(rule_engine_router, tags=["Module 4 — Rule Engine"])
app.include_router(doc_extraction_router, tags=["Module 5 — Document Extraction"])
app.include_router(reporting_router, tags=["Module 6 — Reporting"])
app.include_router(rule_graph_router, tags=["Module 7 — Rule Graph"])


@app.get("/", tags=["Health"])
def health() -> dict:
    return {"status": "ok", "service": "PolicyPilot API"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
