"""
Module 2 — Rule Extraction and Conflict Detection
POST /extract-rules

Makes two sequential Groq API calls:
  Call 1 — Rule extraction from policy Markdown
  Call 2 — Conflict detection across extracted rules

Returns a merged ExtractionResponse combining both results.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.schemas import (
    ConflictObject,
    ExtractionResponse,
    ExtractionSummary,
    KNOWN_ACTIONS,
    Rule,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Groq client (lazy init so startup doesn't crash if key is missing at import)
# ---------------------------------------------------------------------------

def _get_client() -> OpenAI:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY environment variable is not set.",
        )
    return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)


# ---------------------------------------------------------------------------
# File-based extraction cache
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def _cache_enabled() -> bool:
    return os.environ.get("SAVE_EXTRACTION_CACHE", "false").strip().lower() == "true"


def _cache_path(markdown: str) -> Path:
    """Return the cache file path for a given document (keyed by SHA-256 hash)."""
    doc_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]
    return _CACHE_DIR / f"extraction_{doc_hash}.json"


def _load_cache(markdown: str) -> dict | None:
    """Return the cached ExtractionResponse dict if it exists, else None."""
    if not _cache_enabled():
        return None
    path = _cache_path(markdown)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"[CACHE HIT] Loaded cached extraction from {path.name} — skipping LLM calls.")
            return data
        except Exception as e:
            logger.warning(f"[CACHE] Failed to read cache file {path}: {e}. Will re-extract.")
    return None


def _save_cache(markdown: str, response_dict: dict) -> None:
    """Persist an ExtractionResponse dict to the cache directory."""
    if not _cache_enabled():
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(markdown)
        path.write_text(json.dumps(response_dict, indent=2), encoding="utf-8")
        logger.info(f"[CACHE SAVED] Extraction result cached to {path.name}.")
    except Exception as e:
        logger.warning(f"[CACHE] Failed to write cache file: {e}")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are an expert AP policy analyst. Your job is to read an Accounts Payable policy document and extract every single business rule into a structured JSON array. You must not miss any rule, threshold, exception, or cross-reference.

== OUTPUT SCHEMA ==

For every rule you extract, return an object with exactly these fields:

- rule_id: string in format AP-XXX-NNN where XXX is a category code (TWM = three-way match, TAX = tax rules, APR = approval matrix, DEV = deviation notifications, QRC = QR code rules). NNN is a zero-padded number.
- source_clause: the exact section and subsection reference, e.g. "Section 2.2(c)"
- description: one plain English sentence describing what this rule does
- condition: a nested object describing when this rule fires (see CONDITION STRUCTURE below)
- action: one of these exact uppercase strings only: AUTO_APPROVE, ROUTE_TO_AP_CLERK, ROUTE_TO_DEPT_HEAD, ESCALATE_TO_FINANCE_CONTROLLER, ESCALATE_TO_CFO, HOLD, REJECT, FLAG, ROUTE_TO_PROCUREMENT, COMPLIANCE_HOLD. Do NOT invent new action strings.
- requires_justification: boolean, true only if the policy explicitly says a justification note or documentation is required
- notification: object with {type: "email", to: [recipient roles], within_minutes: integer}. Set to null if no notification is mentioned for this rule.
- confidence_score: float 0–1. Use >= 0.95 for unambiguous rules. Use 0.7–0.94 if there is ambiguity or you resolved a cross-reference. Use < 0.7 if you were uncertain about the condition or action.
- raw_text: the exact sentence(s) from the source document, copied verbatim
- conflict_with: always set to empty array []
- suggested_fix: always set to null
- review_status: always set to "pending"

== CONDITION STRUCTURE ==

Every condition node has this shape:
{
  "operator": "GT" | "LT" | "GTE" | "LTE" | "EQ" | "NEQ" | "PCT_DIFF" | "BETWEEN" | "AND" | "OR" | "IS_NULL" | "IS_NOT_NULL",
  "left": field reference string or numeric literal (used by leaf operators only),
  "right": field reference string, numeric literal, or boolean (used by leaf operators only),
  "threshold": number (PCT_DIFF only),
  "direction": "above" or "below" (PCT_DIFF only),
  "lower": number (BETWEEN only),
  "upper": number (BETWEEN only),
  "operands": array of nested condition objects (AND/OR only)
}

CRITICAL RULE for AND/OR: When operator is AND or OR, place the sub-conditions inside the "operands" array. Do NOT put condition objects inside "left" or "right" — those fields must be null for AND/OR nodes. This is the single most important structural rule.

Set unused fields to null. Do not omit them.

== FIELD VOCABULARY ==

Always use these exact table.field references:
- Invoice_table: amount (line/total amount), grand_total (invoice grand total including taxes), qty, rate, date, invoice_number, po_number, taxable_amount, tax_amount, cgst, sgst, igst, supply_type, place_of_supply, gstin
- PO_table: amount, qty, rate, date, po_number
- GRN_table: qty, date, grn_number, po_number
- Vendor_table: gstin, pan, watchlist (boolean flag)

IMPORTANT — amount vs grand_total: Use "Invoice_table.amount" when the policy refers to the invoice total, invoice value, or invoice amount in the context of comparing against PO amounts. Use "Invoice_table.grand_total" ONLY when the policy explicitly says "grand total" or refers to the final amount inclusive of all taxes. When in doubt, use "Invoice_table.amount".

== HANDLING AMBIGUITY ==

If the policy text is genuinely ambiguous (e.g. "notification is at the discretion of the clerk"), set the ambiguous field to null and lower the confidence_score to between 0.7 and 0.85. Do not guess — prefer null with a lower confidence over an incorrect value with a high confidence.

== CROSS-REFERENCE RESOLUTION ==

If any clause says "Refer Section X.Y(Z)" or "as defined in Section X", look up that referenced section in the document, extract the logic from it, and include it inline in the condition of the current rule. Do not leave string references like "see section 2.3b" in the condition. Set confidence_score slightly lower for any rule where you resolved a cross-reference.

== WHAT NOT TO DO ==

- Do NOT invent action strings outside the provided list
- Do NOT put condition objects inside left/right fields — use operands for AND/OR
- Do NOT return markdown, explanations, or commentary — only the JSON object
- Do NOT use string values like "100000" for numeric fields — use the number 100000
- Do NOT set boolean fields like watchlist to integer 1 or 0 — use true or false
- Do NOT omit null fields from the condition object — include all fields set to null

== EXAMPLE ==

Given this policy text: "Invoices above INR 50,00,000 must be escalated to the CFO with mandatory audit documentation."

Correct output rule:
{
  "rule_id": "AP-APR-001",
  "source_clause": "Section 3.2",
  "description": "Escalate invoices above INR 50 lakh to the CFO with mandatory audit documentation",
  "condition": {
    "operator": "GT",
    "left": "Invoice_table.amount",
    "right": 5000000,
    "threshold": null,
    "direction": null,
    "lower": null,
    "upper": null,
    "operands": null
  },
  "action": "ESCALATE_TO_CFO",
  "requires_justification": true,
  "notification": null,
  "confidence_score": 0.95,
  "raw_text": "Invoices above INR 50,00,000 must be escalated to the CFO with mandatory audit documentation.",
  "conflict_with": [],
  "suggested_fix": null,
  "review_status": "pending"
}

Return a JSON object with a single key "rules" containing a valid JSON array of these rule objects. No explanations or markdown."""

CONFLICT_DETECTION_SYSTEM_PROMPT = """You are an expert policy auditor specialising in logical contradiction analysis. You will receive a JSON array of business rules extracted from a policy document. Your task is to identify genuine conflicts between rule pairs using rigorous logical reasoning.

== REASONING PRINCIPLES ==

Before flagging any conflict you MUST apply these principles in order. If any principle eliminates the pair, stop and move to the next pair.

Principle 1 — Simultaneous activation test. A conflict requires at least one concrete input that causes BOTH rules to fire at the same time. Mentally construct a specific example input with actual values. If you cannot construct one because the conditions cover non-overlapping ranges, different fields, or mutually exclusive states, the rules are complementary. Do not flag them.

Principle 2 — Action contradiction test. Even if two rules fire on the same input, they conflict ONLY if their actions are mutually exclusive. Two rules that both route to human approvers (even different ones at different levels) are NOT contradictory — escalation priority resolves them. Two rules with the exact same action can NEVER conflict regardless of how different their conditions look.

Principle 3 — Complementary range recognition. Rules that partition a numeric space into non-overlapping bands are complementary by design. A rule firing below threshold X and another firing above threshold Y (where Y > X) cannot both be true. Do not flag these.

Principle 4 — Field naming tolerance. Two rules referencing slightly different field names for the same business concept is a data quality issue, not a logical conflict. Ignore naming inconsistencies entirely.

Principle 5 — Override vs conflict. When a specific rule is a subset of a general rule, this is an intentional override. Only flag it if the actions are genuinely contradictory for the overlapping inputs. If the specific rule refines or adds to the general rule, it is not a conflict.

Principle 6 — Suggested fix constraints. The suggested_fix must be a valid rule object matching the input structure exactly. The action must be a plain uppercase string. Do NOT invent new operators, IF/THEN/ELSE structures, or conditional action objects. If you cannot construct a valid fix, set suggested_fix to null.

== CONFLICT DIRECTION ==

Always assign rule_id_a to the MORE GENERAL rule and rule_id_b to the MORE SPECIFIC rule. The suggested_fix replaces rule_id_b (the specific rule) by merging logic from both.

== SEVERITY CLASSIFICATION ==

Add a "severity" field to each conflict object:
- "critical": one rule auto-approves or auto-processes while the other rejects, holds, or blocks the same input (e.g. AUTO_APPROVE vs REJECT)
- "high": one rule routes to a fast-track action while the other requires escalation to a senior approver for the same input (e.g. AUTO_APPROVE vs ESCALATE_TO_CFO)
- "medium": two rules route to different human approvers and neither is clearly higher priority
- "low": rules partially overlap but actions are compatible or one is clearly an intentional refinement

== OUTPUT SCHEMA ==

For each genuine conflict return:
- conflict_id: string in format CONF-NNN
- rule_id_a: rule_id of the more general rule
- rule_id_b: rule_id of the more specific rule
- severity: "critical" | "high" | "medium" | "low"
- explanation: plain English including the specific example input where both rules fire and produce contradictory outcomes. If you cannot describe such an input, do not include this conflict.
- suggested_fix: a complete replacement rule object for rule_id_b that resolves the contradiction. Action must be a plain string. source_clause must reference both original rules. Set to null if you cannot construct a valid fix.

== EXAMPLE — GENUINE CONFLICT ==

Rule A: "Invoices under 100000 → AUTO_APPROVE"
Rule B: "Watchlist vendor invoices regardless of amount → ROUTE_TO_DEPT_HEAD"
Example input: Invoice amount = 50000, vendor is on watchlist.
Both rules fire. Rule A says auto-approve, Rule B says route to dept head. These actions are contradictory — this IS a conflict with severity "high".

== EXAMPLE — NOT A CONFLICT ==

Rule A: "Invoices within 1% of PO amount → AUTO_APPROVE"
Rule B: "Invoices under PO amount by more than 5% → FLAG"
These cover non-overlapping ranges (within 1% vs more than 5% below). No single input can trigger both. This is NOT a conflict.

Return a JSON object with key "conflicts" containing a JSON array of conflict objects. No markdown or explanations outside the JSON. If no genuine conflicts exist, return an empty array."""


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ExtractRulesRequest(BaseModel):
    markdown: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_section(source_clause: str) -> str:
    """
    Extract the top-level section identifier from a source_clause string.
    Examples:
        "Section 2.2(c)"  → "Section 2"
        "Section 4.1"     → "Section 4"
        "Appendix A"      → "Appendix A"
    Falls back to the raw string if no pattern matches.
    """
    m = re.match(r"(Section\s+\d+)", source_clause, re.IGNORECASE)
    if m:
        return m.group(1)
    return source_clause.strip() or "Unknown"


# Maps common LLM-invented synonyms to the canonical KNOWN_ACTIONS string.
_ACTION_ALIASES: Dict[str, str] = {
    "ROUTE_TO_FINANCE_CONTROLLER": "ESCALATE_TO_FINANCE_CONTROLLER",
    "ESCALATE_TO_DEPT_HEAD": "ROUTE_TO_DEPT_HEAD",
    "ROUTE_TO_CFO": "ESCALATE_TO_CFO",
    "APPROVE": "AUTO_APPROVE",
    "AUTO_REJECT": "REJECT",
    "MANUAL_HOLD": "HOLD",
    "ROUTE_TO_COMPLIANCE": "COMPLIANCE_HOLD",
}


def _normalize_actions(rules: List[Dict[str, Any]]) -> None:
    """
    For each rule dict, normalise the action string:
      1. Upper-case the raw value first so 'auto_approve' == 'AUTO_APPROVE'.
      2. If it exactly matches a KNOWN_ACTION, keep it.
      3. If it matches an alias, remap it to the canonical name and log.
      4. If it is completely unknown, keep it as-is but add a schema_warning.
      5. If it is null/empty, inject a placeholder and warn the user.
    Mutates the list in-place.
    """
    for rule in rules:
        action = rule.get("action")

        # Handle null/None or empty string
        if not action:
            rule["action"] = "ACTION_MISSING_PLEASE_UPDATE"
            warnings = rule.get("schema_warnings", [])
            valid_actions = ", ".join(sorted(KNOWN_ACTIONS))
            warnings.append(
                f"Policy did not specify an action. Review the raw_text above and update the action to one of the following: {valid_actions}"
            )
            rule["schema_warnings"] = warnings
            logger.warning(f"[ACTION WARNING] {rule.get('rule_id', '?')}: Action was null. Flagged for review.")
            continue

        # Fix 1: upper-case before any comparison so 'auto_approve' maps to 'AUTO_APPROVE'
        action_upper = action.upper()
        rule["action"] = action_upper

        if action_upper in KNOWN_ACTIONS:
            continue  # perfect match after upper-casing

        canonical = _ACTION_ALIASES.get(action_upper)
        if canonical:
            logger.info(
                f"[ACTION REMAP] {rule.get('rule_id', '?')}: "
                f"{action_upper} → {canonical}"
            )
            rule["action"] = canonical
        else:
            logger.warning(
                f"[ACTION WARNING] {rule.get('rule_id', '?')}: "
                f"Unrecognized action '{action_upper}' — not in KNOWN_ACTIONS. "
                f"Rule kept as-is; review recommended."
            )
            warnings = rule.get("schema_warnings", [])
            warnings.append(f"action: Unrecognized action '{action_upper}'")
            rule["schema_warnings"] = warnings


def _normalize_condition_node(node: Any) -> None:
    """Recursively normalise a single condition node dict in-place."""
    if not isinstance(node, dict):
        return

    # Coerce left/right string numerics to float
    for field in ("left", "right"):
        val = node.get(field)
        if isinstance(val, str):
            try:
                node[field] = float(val)
            except (ValueError, TypeError):
                pass  # keep as string (e.g. field references or 'today()')

    # Coerce right string booleans
    right = node.get("right")
    if isinstance(right, str):
        if right.lower() == "true":
            node["right"] = True
        elif right.lower() == "false":
            node["right"] = False

    # Watchlist int 1 -> boolean True coercion
    operator = node.get("operator")
    left_field = node.get("left")
    if operator == "EQ" and node.get("right") == 1 and isinstance(left_field, str):
        if left_field.endswith(".watchlist") or "watchlist" in left_field.lower():
            node["right"] = True
            logger.info("[NORMALIZE] Converted watchlist right value from 1 to True.")

    # Coerce lower/upper string numerics to float
    for field in ("lower", "upper"):
        val = node.get(field)
        if isinstance(val, str):
            try:
                node[field] = float(val)
            except (ValueError, TypeError):
                pass

    # Recurse into operands (AND / OR nodes)
    operands = node.get("operands")
    if isinstance(operands, list):
        for operand in operands:
            _normalize_condition_node(operand)


def _normalize_condition_values(rules: List[Dict[str, Any]]) -> None:
    """Walk every rule's condition tree and coerce string numerics / booleans."""
    for rule in rules:
        condition = rule.get("condition")
        if isinstance(condition, dict):
            _normalize_condition_node(condition)


def _validate_suggested_fix(rules: List[Dict[str, Any]]) -> None:
    """
    For each rule that has a suggested_fix:
      - Verify suggested_fix.action is a plain string. If not, nullify the fix.
      - If the fix is valid, run _normalize_condition_values on its condition.
    Mutates the list in-place.
    """
    for rule in rules:
        fix = rule.get("suggested_fix")
        if fix is None:
            continue
        if not isinstance(fix, dict):
            logger.warning(
                f"[SUGGESTED FIX WARNING] rule_id: {rule.get('rule_id', '?')} "
                f"— suggested_fix is not an object, nullifying suggested_fix"
            )
            rule["suggested_fix"] = None
            continue

        fix_action = fix.get("action")
        if not isinstance(fix_action, str):
            logger.warning(
                f"[SUGGESTED FIX WARNING] rule_id: {rule.get('rule_id', '?')} "
                f"— suggested_fix.action is not a string, nullifying suggested_fix"
            )
            rule["suggested_fix"] = None
            continue

        # Upper-case the suggested_fix action too
        fix["action"] = fix_action.upper()

        # Normalise condition values inside the fix
        fix_condition = fix.get("condition")
        if isinstance(fix_condition, dict):
            _normalize_condition_node(fix_condition)


def _normalize_and_or_structure_node(node: Any) -> None:
    if not isinstance(node, dict):
        return

    operator = node.get("operator")
    if operator in ("AND", "OR"):
        left = node.get("left")
        right = node.get("right")
        if isinstance(left, dict) and isinstance(right, dict):
            node["operands"] = [left, right]
            node["left"] = None
            node["right"] = None

    operands = node.get("operands")
    if isinstance(operands, list):
        for op in operands:
            _normalize_and_or_structure_node(op)


def _normalize_and_or_structure(rules: List[Dict[str, Any]]) -> None:
    for rule in rules:
        condition = rule.get("condition")
        if isinstance(condition, dict):
            _normalize_and_or_structure_node(condition)

        fix = rule.get("suggested_fix")
        if isinstance(fix, dict):
            fix_cond = fix.get("condition")
            if isinstance(fix_cond, dict):
                _normalize_and_or_structure_node(fix_cond)


def _safe_parse_json_array(raw: str, call_name: str) -> List[Dict[str, Any]]:
    """
    Strip markdown fences and parse JSON array.
    If the JSON is truncated (common with large outputs), salvages all
    complete objects found before the cut-off point.
    """
    text = raw.strip()
    # Strip code fences if the model forgot the prompt instruction
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    text = text.strip()

    # ---- Attempt 1: parse as-is ----------------------------------------
    try:
        result = json.loads(text)
        
        # Unwrap if using structured JSON mode: {"rules": [...]} or {"conflicts": [...]}
        if isinstance(result, dict):
            if "rules" in result:
                result = result["rules"]
            elif "conflicts" in result:
                result = result["conflicts"]
                
        if isinstance(result, list):
            logger.info(f"{call_name}: parsed {len(result)} items cleanly.")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ---- Attempt 2: salvage complete objects from a truncated array -----
    logger.warning(f"{call_name}: JSON malformed/truncated — attempting recovery...")
    # Walk backwards from the end to find the last complete '}'
    # then close the array and retry
    last_brace = text.rfind("}")
    if last_brace != -1:
        repaired = text[: last_brace + 1].rstrip().rstrip(",") + "\n]"
        # If text didn't start with '[', wrap it
        if not repaired.lstrip().startswith("["):
            repaired = "[" + repaired
        try:
            result = json.loads(repaired)
            if isinstance(result, list) and len(result) > 0:
                logger.warning(
                    f"{call_name}: Recovered {len(result)} complete objects "
                    f"from truncated response (original length={len(text)} chars)."
                )
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # ---- Give up --------------------------------------------------------
    logger.error(f"Failed to parse {call_name} response.\nRaw (first 800 chars):\n{text[:800]}")
    raise HTTPException(
        status_code=502,
        detail=(
            f"LLM returned malformed JSON from {call_name}. "
            "Try with a shorter policy document or re-run the extraction."
        ),
    )


def _compute_summary(rules: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]) -> ExtractionSummary:
    high = sum(1 for r in rules if r.get("confidence_score", 0) >= 0.9)
    med = sum(1 for r in rules if 0.7 <= r.get("confidence_score", 0) < 0.9)
    low = sum(1 for r in rules if r.get("confidence_score", 0) < 0.7)
    return ExtractionSummary(
        total_rules=len(rules),
        high_confidence=high,
        medium_confidence=med,
        low_confidence=low,
        conflicts_found=len(conflicts),
    )


def _merge_conflicts_into_rules(
    rules: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rule_index: Dict[str, Dict[str, Any]] = {r["rule_id"]: r for r in rules}
    for conflict in conflicts:
        rule_id_b = conflict.get("rule_id_b")
        rule_id_a = conflict.get("rule_id_a")
        suggested_fix = conflict.get("suggested_fix")
        if rule_id_b and rule_id_b in rule_index:
            rule = rule_index[rule_id_b]
            existing_conflicts = rule.get("conflict_with", [])
            if rule_id_a and rule_id_a not in existing_conflicts:
                existing_conflicts.append(rule_id_a)
            rule["conflict_with"] = existing_conflicts
            if suggested_fix is not None:
                rule["suggested_fix"] = suggested_fix
    return rules


def _extract_numeric_range(condition: Dict[str, Any]) -> tuple:
    """
    Extract (field, lower_bound, upper_bound) from a leaf condition node.
    Returns (None, None, None) if the condition is not a simple numeric comparison.
    """
    op = condition.get("operator")
    left = condition.get("left")
    right = condition.get("right")
    lower = condition.get("lower")
    upper = condition.get("upper")

    if not isinstance(left, str):
        return (None, None, None)

    if op == "BETWEEN" and lower is not None and upper is not None:
        try:
            return (left, float(lower), float(upper))
        except (ValueError, TypeError):
            return (None, None, None)

    if op in ("GT", "GTE") and isinstance(right, (int, float)):
        return (left, float(right), float("inf"))

    if op in ("LT", "LTE") and isinstance(right, (int, float)):
        return (left, float("-inf"), float(right))

    if op == "PCT_DIFF":
        threshold = condition.get("threshold")
        direction = condition.get("direction")
        if threshold is not None and direction is not None:
            try:
                t = float(threshold)
                # Represent as a synthetic range so we can compare direction
                if direction == "above":
                    return (f"{left}__pct_diff_above", t, float("inf"))
                elif direction == "below":
                    return (f"{left}__pct_diff_below", float("-inf"), t)
            except (ValueError, TypeError):
                pass

    return (None, None, None)


def _ranges_overlap(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    """Return True if numeric ranges [lo_a, hi_a] and [lo_b, hi_b] overlap."""
    return lo_a <= hi_b and lo_b <= hi_a


def _validate_conflicts(
    conflicts: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Post-processing filter for LLM-generated conflicts.
    Removes false positives where:
      1. Both rules have non-overlapping numeric ranges on the same field.
      2. Both rules produce the exact same action.
      3. The suggested_fix action is not a plain string.
    Returns the cleaned conflicts list.
    """
    rule_index: Dict[str, Dict[str, Any]] = {r["rule_id"]: r for r in rules}
    cleaned: List[Dict[str, Any]] = []

    for conflict in conflicts:
        conflict_id = conflict.get("conflict_id", "<unknown>")
        rule_a = rule_index.get(conflict.get("rule_id_a", ""))
        rule_b = rule_index.get(conflict.get("rule_id_b", ""))

        if not rule_a or not rule_b:
            cleaned.append(conflict)
            continue

        # --- Filter 1: identical actions cannot conflict ---
        action_a = rule_a.get("action")
        action_b = rule_b.get("action")
        if action_a and action_b and action_a == action_b:
            logger.info(
                f"[CONFLICT FILTERED] conflict_id: {conflict_id} — "
                f"reason: both rules produce identical action '{action_a}'"
            )
            continue

        # --- Filter 2: non-overlapping numeric ranges ---
        cond_a = rule_a.get("condition", {})
        cond_b = rule_b.get("condition", {})
        field_a, lo_a, hi_a = _extract_numeric_range(cond_a)
        field_b, lo_b, hi_b = _extract_numeric_range(cond_b)

        if (
            field_a is not None
            and field_b is not None
            and field_a == field_b
            and not _ranges_overlap(lo_a, hi_a, lo_b, hi_b)
        ):
            logger.info(
                f"[CONFLICT FILTERED] conflict_id: {conflict_id} — "
                f"reason: non-overlapping ranges on field '{field_a}' "
                f"([{lo_a}, {hi_a}] vs [{lo_b}, {hi_b}])"
            )
            continue

        # --- Filter 3: malformed suggested_fix action ---
        fix = conflict.get("suggested_fix")
        if fix is not None:
            if not isinstance(fix, dict):
                conflict["suggested_fix"] = None
                logger.info(
                    f"[CONFLICT FILTERED] conflict_id: {conflict_id} — "
                    f"reason: suggested_fix is not a dict, nullified"
                )
            else:
                fix_action = fix.get("action")
                if not isinstance(fix_action, str):
                    logger.info(
                        f"[CONFLICT FILTERED] conflict_id: {conflict_id} — "
                        f"reason: suggested_fix.action is not a plain string, nullified"
                    )
                    conflict["suggested_fix"] = None

        cleaned.append(conflict)

    if len(conflicts) != len(cleaned):
        logger.info(
            f"[CONFLICT VALIDATION] Filtered {len(conflicts) - len(cleaned)} "
            f"false-positive conflicts. {len(cleaned)} genuine conflicts remain."
        )
    else:
        logger.info(
            f"[CONFLICT VALIDATION] All {len(cleaned)} conflicts passed validation."
        )

    return cleaned


def _validate_with_warnings(
    items: List[Dict[str, Any]],
    model_cls,
    call_name: str,
    id_field: str = "rule_id",
) -> List[Dict[str, Any]]:
    """
    Soft-validate every dict in `items` against `model_cls`.
    Does NOT raise. Instead:
      - attaches schema_warnings (list[str]) to each item
      - logs [SCHEMA WARNING] per failing item
      - logs one [SCHEMA SUMMARY] line at the end
    Returns the same list, unmodified except for the schema_warnings field.
    """
    from pydantic import ValidationError

    valid_count = 0
    warning_count = 0

    for item in items:
        item_id = item.get(id_field, "<unknown>")
        try:
            model_cls.model_validate(item)
            item["schema_warnings"] = []
            valid_count += 1
        except ValidationError as exc:
            field_names = [".".join(str(loc) for loc in e["loc"]) for e in exc.errors()]
            messages = [f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()]
            item["schema_warnings"] = messages
            logger.warning(
                f"[SCHEMA WARNING] {id_field}: {item_id} — missing or invalid fields: {field_names}"
            )
            warning_count += 1

    logger.info(
        f"[SCHEMA SUMMARY] {call_name}: {len(items)} items parsed, "
        f"{valid_count} valid, {warning_count} with warnings"
    )
    return items


# ---------------------------------------------------------------------------
# Sync helper — runs the two blocking Groq calls, safe for asyncio.to_thread
# ---------------------------------------------------------------------------

def _run_extraction_sync(markdown: str, client: OpenAI) -> ExtractionResponse:
    """Blocking function: two sequential Groq calls. Called via asyncio.to_thread."""

    # ---- Call 1: Rule Extraction ----------------------------------------
    logger.info("Starting Groq Call 1 — Rule Extraction")
    call1 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0,
        max_tokens=6000, 
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": markdown},
        ],
    )
    raw_rules_json = call1.choices[0].message.content
    logger.info(f"Call 1 complete. Raw length: {len(raw_rules_json)} chars")

    rules_list = _safe_parse_json_array(raw_rules_json, "Rule Extraction (Call 1)")

    for rule in rules_list:
        rule.setdefault("conflict_with", [])
        rule.setdefault("suggested_fix", None)
        rule.setdefault("review_status", "pending")
        # Derive top-level section from source_clause for downstream grouping
        rule["section"] = _derive_section(rule.get("source_clause", ""))

    # ---- Step 1: Normalize actions (upper-case, remap synonyms, warn) ----
    _normalize_actions(rules_list)

    # ---- Step 2: Normalize condition numeric/boolean string values --------
    _normalize_condition_values(rules_list)

    # ---- Step 3: Validate suggested_fix objects (nullify bad fixes) -------
    _validate_suggested_fix(rules_list)

    # ---- Step 3.5: Convert AND/OR left/right to operands array ------------
    _normalize_and_or_structure(rules_list)

    # ---- Step 4: Schema validation pass (warn, do not drop) ---------------
    _validate_with_warnings(rules_list, Rule, "Rule Extraction (Call 1)")

    # ---- Replenish Token Bucket -----------------------------------------
    # Groq's free tier is 12,000 Tokens/Min (replenishes 200 tokens/sec).
    # Since Call 1 uses ~6,000 tokens, we pause briefly to refill the 
    # bucket so Call 2 stays under the rolling 1-minute limit.
    logger.info("Delaying for 15 seconds to replenish Groq free-tier tokens...")
    time.sleep(15)

    # ---- Call 2: Conflict Detection -------------------------------------
    logger.info("Starting Groq Call 2 — Conflict Detection")
    call2 = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0,
        max_tokens=5000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CONFLICT_DETECTION_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(rules_list)},
        ],
    )
    raw_conflicts_json = call2.choices[0].message.content
    logger.info(f"Call 2 complete. Raw length: {len(raw_conflicts_json)} chars")

    conflicts_list = _safe_parse_json_array(raw_conflicts_json, "Conflict Detection (Call 2)")

    # ---- Schema validation pass (warn, do not drop) ----------------------
    _validate_with_warnings(
        conflicts_list, ConflictObject, "Conflict Detection (Call 2)", id_field="conflict_id"
    )

    # ---- Post-processing: filter false-positive conflicts -----------------
    conflicts_list = _validate_conflicts(conflicts_list, rules_list)

    # ---- Merge & validate -----------------------------------------------
    merged_rules = _merge_conflicts_into_rules(rules_list, conflicts_list)

    # Convert clean rules from pending to accepted
    for rule in merged_rules:
        try:
            confidence = float(rule.get("confidence_score", 0.0))
        except (ValueError, TypeError):
            confidence = 0.0
            
        has_conflicts = len(rule.get("conflict_with", [])) > 0
        if confidence >= 0.9 and not has_conflicts:
            rule["review_status"] = "accepted"

    summary = _compute_summary(merged_rules, conflicts_list)
    validated_rules = [Rule.model_validate(r) for r in merged_rules]
    validated_conflicts = [ConflictObject.model_validate(c) for c in conflicts_list]

    return ExtractionResponse(
        rules=validated_rules,
        conflicts=validated_conflicts,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/extract-rules", response_model=ExtractionResponse)
async def extract_rules(body: ExtractRulesRequest) -> ExtractionResponse:
    """
    Two sequential Groq API calls:
      1. Extract rules from policy Markdown
      2. Detect conflicts across extracted rules
    Returns merged result.

    If SAVE_EXTRACTION_CACHE=true in .env and a cache file for this exact
    document already exists, returns the cached result immediately (zero tokens).
    """
    try:
        # ---- Cache check (no API call if hit) ----------------------------
        cached = _load_cache(body.markdown)
        if cached is not None:
            return ExtractionResponse.model_validate(cached)

        # ---- Cache miss: run real extraction -----------------------------
        client = _get_client()
        result = await asyncio.to_thread(_run_extraction_sync, body.markdown, client)

        # ---- Persist result for future runs ------------------------------
        _save_cache(body.markdown, result.model_dump(mode="json"))

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Extraction Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Extraction failed: {str(e)}",
        )
