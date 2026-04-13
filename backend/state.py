"""
backend/state.py — Shared in-memory state for PolicyPilot.

This is the ONLY place active_ruleset is declared.
Both finalization.py and rule_engine.py import from here.
Never import state from finalization.py — that creates a circular import.

Persistence:
  On startup, this module auto-loads backend/db/active_ruleset.json if it
  exists so the ruleset survives server restarts without user action.
"""
import json
import logging
from pathlib import Path
from typing import List, Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage path — finalization.py writes here; this module reads on startup.
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent / "db" / "active_ruleset.json"

# ---------------------------------------------------------------------------
# The active finalized ruleset. Stored as plain dicts (not Pydantic objects)
# so it can be freely serialised and shared across modules without re-validation.
# ---------------------------------------------------------------------------
active_ruleset: List[Any] = []

# SHA-256 hash of the finalized ruleset JSON. Used as a unique Policy ID to
# link execution history records back to the exact policy version that governed them.
active_ruleset_hash: Optional[str] = None

# ---------------------------------------------------------------------------
# Auto-load from disk on import (i.e., on every server startup)
# ---------------------------------------------------------------------------
def _load_from_disk() -> None:
    """Populate active_ruleset and active_ruleset_hash from the persisted JSON file."""
    if not DB_PATH.exists():
        return
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
        rules = data.get("rules", [])
        policy_id = data.get("policy_id")
        if rules:
            active_ruleset.clear()
            active_ruleset.extend(rules)
            global active_ruleset_hash
            active_ruleset_hash = policy_id
            logger.info(
                f"[STATE] Auto-loaded {len(rules)} rules from disk "
                f"(policy_id={policy_id}). Server restart safe."
            )
    except Exception as e:
        logger.warning(f"[STATE] Could not load persisted ruleset: {e}")

_load_from_disk()
