"""
Module 3 — Rule Finalization
POST /finalize-rules  — store reviewed ruleset in shared state
GET  /active-rules    — retrieve current active ruleset

State lives in backend/state.py — do NOT declare it here.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import state  # shared mutable store
from models.schemas import FinalizeRulesResponse

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request model (we accept raw dicts so rules that were edited in the frontend
# JSON editor don't get rejected by strict Pydantic validation here)
# ---------------------------------------------------------------------------

class FinalizeRulesRequest(BaseModel):
    rules: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/finalize-rules", response_model=FinalizeRulesResponse)
async def finalize_rules(body: FinalizeRulesRequest) -> FinalizeRulesResponse:
    """
    Replace the active ruleset with the caller's reviewed rules array.
    Each rule must already have review_status set to one of:
      accepted | modified | kept_original
    Rules still in 'pending' status are rejected to enforce the review workflow.
    """
    if not body.rules:
        raise HTTPException(status_code=400, detail="Rules array must not be empty.")

    pending = [r for r in body.rules if r.get("review_status") == "pending"]
    if pending:
        pending_ids = [r.get("rule_id", "unknown") for r in pending]
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(pending)} rule(s) still have status 'pending' and have not been reviewed: "
                f"{pending_ids}. Action all flagged rules before finalizing."
            ),
        )

    # Atomically replace the shared list contents
    state.active_ruleset.clear()
    state.active_ruleset.extend(body.rules)

    # Generate a deterministic SHA-256 policy ID from the finalized rules.
    # Used to link document execution history logs back to this exact ruleset.
    ruleset_json = json.dumps(body.rules, sort_keys=True, ensure_ascii=False)
    state.active_ruleset_hash = hashlib.sha256(ruleset_json.encode("utf-8")).hexdigest()[:16]

    # ---- Persist to disk so the ruleset survives server restarts ----------
    try:
        from datetime import datetime, timezone
        state.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        persist_payload = {
            "rules": body.rules,
            "policy_id": state.active_ruleset_hash,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "total": len(body.rules),
        }
        state.DB_PATH.write_text(
            json.dumps(persist_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"[PERSIST] Ruleset saved to disk at {state.DB_PATH}")
    except Exception as e:
        logger.warning(f"[PERSIST] Could not save ruleset to disk: {e}")

    accepted = sum(1 for r in body.rules if r.get("review_status") == "accepted")
    modified = sum(1 for r in body.rules if r.get("review_status") == "modified")
    kept = sum(1 for r in body.rules if r.get("review_status") == "kept_original")

    logger.info(
        f"Ruleset finalized: {len(body.rules)} rules — policy_id={state.active_ruleset_hash} "
        f"(accepted={accepted}, modified={modified}, kept_original={kept})"
    )

    return FinalizeRulesResponse(
        accepted=accepted,
        modified=modified,
        kept_original=kept,
        total=len(body.rules),
        message=f"Ruleset finalized with {len(body.rules)} rules. Policy ID: {state.active_ruleset_hash}",
    )


@router.get("/active-rules")
async def get_active_rules() -> Dict[str, Any]:
    """Return the currently active finalized ruleset."""
    if not state.active_ruleset:
        return {
            "rules": [],
            "total": 0,
            "message": "No ruleset has been finalized yet.",
        }
    return {
        "rules": state.active_ruleset,
        "total": len(state.active_ruleset),
    }
