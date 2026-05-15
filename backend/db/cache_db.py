"""
backend/db/cache_db.py — SQLite-backed extraction cache for PolicyPilot.

Uses Python's built-in sqlite3 — zero external dependencies.

Table: policy_extractions
  Stores per-document extraction results, keyed by a SHA-256 hash of the
  policy markdown text so the same document always hits the same row.

Columns:
  doc_hash               — 16-char SHA-256 prefix (PRIMARY KEY)
  filename               — original uploaded filename
  cached_at              — ISO-8601 UTC timestamp
  raw_llm_rules_json     — raw string returned by LLM Call 1 (BEFORE any
                           normalization, action remapping, or schema validation)
  raw_llm_conflicts_json — raw string returned by LLM Call 2 (BEFORE filtering)
  extraction_result_json — fully processed ExtractionResponse as JSON string
                           (what the /extract-rules endpoint actually returns)

Priority order used by extraction.py:
  1. SQLite DB  (this module)
  2. File cache (backend/cache/*.json, existing system)
  3. Live LLM calls
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB location — sits alongside the existing active_ruleset.json in backend/db/
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).resolve().parent / "policy_cache.db"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    """
    Open a connection to the SQLite database.
    Uses check_same_thread=False so the connection can be used from asyncio
    thread-pool workers (asyncio.to_thread). Each caller is responsible for
    closing the connection after use.
    """
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # allows dict-style column access
    return conn


def _ensure_table() -> None:
    """Create the policy_extractions table if it does not already exist."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_extractions (
                doc_hash               TEXT PRIMARY KEY,
                filename               TEXT,
                cached_at              TEXT,
                raw_llm_rules_json     TEXT,
                raw_llm_conflicts_json TEXT,
                extraction_result_json TEXT
            )
            """
        )
        conn.commit()
        logger.debug("[DB] policy_extractions table ensured.")
    except Exception as e:
        logger.error(f"[DB] Failed to create policy_extractions table: {e}")
        raise
    finally:
        conn.close()


# Run table creation once at import time (lightweight, idempotent).
_ensure_table()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached_extraction(
    doc_hash: str,
) -> Optional[Tuple[str, str, dict]]:
    """
    Look up a cached extraction by document hash.

    Returns:
        (raw_llm_rules_json, raw_llm_conflicts_json, extraction_result_dict)
        or None if no cache entry exists.

    The caller receives the raw LLM strings as well as the processed result
    so it can inspect/log what the model originally returned.
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            """
            SELECT raw_llm_rules_json,
                   raw_llm_conflicts_json,
                   extraction_result_json
            FROM   policy_extractions
            WHERE  doc_hash = ?
            """,
            (doc_hash,),
        ).fetchone()

        if row is None:
            return None

        raw_rules = row["raw_llm_rules_json"] or ""
        raw_conflicts = row["raw_llm_conflicts_json"] or ""
        result_dict = json.loads(row["extraction_result_json"])

        logger.info(
            f"[DB CACHE HIT] doc_hash={doc_hash} — "
            "returning cached ExtractionResponse, skipping LLM calls."
        )
        return raw_rules, raw_conflicts, result_dict

    except Exception as e:
        logger.warning(f"[DB] Failed to read cache for doc_hash={doc_hash}: {e}")
        return None
    finally:
        conn.close()


def save_extraction(
    doc_hash: str,
    filename: str,
    raw_llm_rules_json: str,
    raw_llm_conflicts_json: str,
    extraction_result: dict,
) -> None:
    """
    Persist an extraction result to the SQLite cache.

    Args:
        doc_hash               — 16-char SHA-256 prefix of the policy markdown
        filename               — original uploaded filename (for human readability)
        raw_llm_rules_json     — raw LLM response string from Call 1 (pre-processing)
        raw_llm_conflicts_json — raw LLM response string from Call 2 (pre-filtering)
        extraction_result      — the fully processed ExtractionResponse dict
    """
    conn = _get_connection()
    try:
        cached_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO policy_extractions
                (doc_hash, filename, cached_at,
                 raw_llm_rules_json, raw_llm_conflicts_json, extraction_result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                doc_hash,
                filename,
                cached_at,
                raw_llm_rules_json,
                raw_llm_conflicts_json,
                json.dumps(extraction_result),
            ),
        )
        conn.commit()
        logger.info(
            f"[DB CACHE SAVED] doc_hash={doc_hash} filename='{filename}' "
            f"cached_at={cached_at}"
        )
    except Exception as e:
        logger.warning(f"[DB] Failed to save cache for doc_hash={doc_hash}: {e}")
    finally:
        conn.close()


def list_cached_policies() -> list:
    """
    Return a summary list of all cached policy documents.
    Used by the optional GET /cached-policies admin endpoint.

    Returns a list of dicts with keys:
        doc_hash, filename, cached_at, rule_count
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT doc_hash, filename, cached_at, extraction_result_json
            FROM   policy_extractions
            ORDER BY cached_at DESC
            """
        ).fetchall()

        result = []
        for row in rows:
            rule_count = 0
            try:
                data = json.loads(row["extraction_result_json"] or "{}")
                rule_count = len(data.get("rules", []))
            except Exception:
                pass

            result.append(
                {
                    "doc_hash": row["doc_hash"],
                    "filename": row["filename"],
                    "cached_at": row["cached_at"],
                    "rule_count": rule_count,
                }
            )

        return result

    except Exception as e:
        logger.warning(f"[DB] Failed to list cached policies: {e}")
        return []
    finally:
        conn.close()


def delete_cached_extraction(doc_hash: str) -> bool:
    """
    Remove a single cache entry by doc_hash.
    Returns True if a row was deleted, False if not found.
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM policy_extractions WHERE doc_hash = ?",
            (doc_hash,),
        )
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"[DB] Deleted cache entry for doc_hash={doc_hash}")
        return deleted
    except Exception as e:
        logger.warning(f"[DB] Failed to delete cache entry doc_hash={doc_hash}: {e}")
        return False
    finally:
        conn.close()
