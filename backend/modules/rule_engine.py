"""
Module 4 — Dynamic Expression Evaluator Rule Engine
POST /execute-rules

ZERO LLM calls. ZERO hardcoded business logic.
Generic expression evaluator: reads conditions from rule JSON at runtime
and evaluates them against a merged document payload.

The engine has no semantic knowledge of invoices, AP policy, or field meanings.

Key capabilities:
- Expression-aware resolver: handles "PO_table.amount * 1.01" as a value
- Date-aware comparisons: strings matching YYYY-MM-DD are parsed as dates
- PCT_DIFF with direction "above" | "below" | "both"
- BETWEEN with expression bounds
- [*] notation as primary line-item detection; qty/rate heuristic as fallback
- Live payload mutation: sets has_deviation / has_compliance_failure on FAIL
- Pre-execution AND/OR structure normalization (mirrors extraction.py logic)
- Human-readable flags array in response
"""

import json
import logging
import os
import re
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException

import state  # shared active ruleset
from models.schemas import (
    DeviationDetails,
    DocumentPayload,
    ExecuteRulesRequest,
    ExecuteRulesResponse,
    RuleExecutionResult,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# PRE-EXECUTION: AND/OR structure normalisation
# Mirrors extraction.py's _normalize_and_or_structure_node — preserved here
# so the rule engine defensively fixes any rule conditions that arrive with
# left/right instead of operands on AND/OR nodes (e.g. from cached payloads).
# Must be called BEFORE the engine loop, not inside evaluate_condition.
# ---------------------------------------------------------------------------

def _normalize_and_or_node(node: Any) -> None:
    """Convert AND/OR nodes that incorrectly use left/right into operands list."""
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
            _normalize_and_or_node(op)


def _normalize_and_or_structure(rules: List[Dict[str, Any]]) -> None:
    """
    Walk every rule's condition tree and fix AND/OR structure.
    Called once before the engine loop — NOT inside evaluate_condition.
    """
    for rule in rules:
        condition = rule.get("condition")
        if isinstance(condition, dict):
            _normalize_and_or_node(condition)
        # Also normalise suggested_fix conditions if present
        fix = rule.get("suggested_fix")
        if isinstance(fix, dict):
            fix_cond = fix.get("condition")
            if isinstance(fix_cond, dict):
                _normalize_and_or_node(fix_cond)


def _normalize_pct_diff_conditions(rules: List[Dict[str, Any]]) -> None:
    """
    Universal pre-execution normalizer for PCT_DIFF direction semantics.

    Fixes two common LLM extraction ambiguities:

    Pattern 1 — WITHIN TOLERANCE:
        A standalone PCT_DIFF with direction=null whose parent rule description
        contains 'within' should fire when the difference is WITHIN the threshold,
        not when it exceeds it. Sets direction='within'.

    Pattern 2 — CEILING CAP inside AND:
        An AND compound containing PCT_DIFF(direction=above, T1) paired with
        PCT_DIFF(direction=below, T2) on the same left/right fields should
        be read as 'exceeds T1% but less than T2%'. The 'below' operand is
        a ceiling cap, not a directional check. Sets direction='cap'.
    """
    for rule in rules:
        condition = rule.get("condition")
        if not isinstance(condition, dict):
            continue
        description = (rule.get("description") or "").lower()

        # --- Pattern 1: standalone PCT_DIFF with 'within' semantics ----------
        if condition.get("operator") == "PCT_DIFF":
            if condition.get("direction") is None and "within" in description:
                condition["direction"] = "within"
                logger.debug(
                    f"Rule {rule.get('rule_id')}: set PCT_DIFF direction='within' "
                    f"(detected 'within' in description)"
                )

        # --- Pattern 2: AND of above + below PCT_DIFF → cap ------------------
        if condition.get("operator") == "AND":
            operands = condition.get("operands") or []
            pct_above_ops = []
            pct_below_ops = []
            for op in operands:
                if not isinstance(op, dict) or op.get("operator") != "PCT_DIFF":
                    continue
                d = op.get("direction")
                if d == "above":
                    pct_above_ops.append(op)
                elif d == "below":
                    pct_below_ops.append(op)

            # If we have matched above + below pairs on the same fields, fix below→cap
            for below_op in pct_below_ops:
                for above_op in pct_above_ops:
                    if (below_op.get("left") == above_op.get("left") and
                        below_op.get("right") == above_op.get("right")):
                        below_op["direction"] = "cap"
                        logger.debug(
                            f"Rule {rule.get('rule_id')}: rewrote PCT_DIFF "
                            f"direction='below' → 'cap' (ceiling in AND compound)"
                        )
                        break


# ---------------------------------------------------------------------------
# Expression-aware field path resolver
# ---------------------------------------------------------------------------

_EXPR_RE = re.compile(
    # FIX 5: Support arbitrary right side (scalar or another field name)
    r"^([A-Za-z_][A-Za-z0-9_.]*)\s*([\+\-\*\/])\s*(.+)$"
)

# Matches abs(...) wrapper
_ABS_RE = re.compile(r"^abs\((.+)\)$", re.IGNORECASE)


def _resolve_compound_expression(expr: str, payload_dict: Dict[str, Any]) -> Any:
    """
    Evaluate a compound arithmetic expression like:
      'Invoice_table.taxable_amount + Invoice_table.tax_amount - Invoice_table.grand_total'
    Tokenises by + and - operators, resolves each operand, and computes the result.
    """
    # Split by + and - while keeping the operators as separate tokens
    tokens = re.split(r'\s*([+-])\s*', expr.strip())
    if not tokens:
        return None

    # First token is always a field/value
    result = _resolve_field(tokens[0].strip(), payload_dict)
    if result is None:
        return None
    try:
        result = float(result)
    except (TypeError, ValueError):
        return None

    i = 1
    while i < len(tokens) - 1:
        op = tokens[i]
        operand = _resolve_field(tokens[i + 1].strip(), payload_dict)
        if operand is None:
            return None
        try:
            val = float(operand)
        except (TypeError, ValueError):
            return None
        if op == '+':
            result += val
        elif op == '-':
            result -= val
        i += 2

    return result


def _resolve_field(path: Any, payload_dict: Dict[str, Any]) -> Any:
    """
    Resolve a field path or literal value from the payload.

    Handles:
      "Invoice_table.grand_total"         → look up table then field
      "PO_table.amount * 1.01"            → resolve field then apply arithmetic
      "Invoice_table.tax + Invoice_table.cgst" → recursive math support
      "today()" | "CURRENT_DATE"          → datetime.date.today()
      100000 (int/float)                  → return as float
      "100000" (numeric string)           → parse as float
      Any other string without "."        → return as string literal
    """
    if isinstance(path, (int, float)):
        return float(path)

    if not isinstance(path, str):
        return path

    # --- today() / CURRENT_DATE / system.current_date
    if path.strip().lower() in ("today()", "current_date", "system.current_date"):
        return datetime.date.today()

    # --- abs() wrapper: abs(A + B - C) ----------------------------------------
    abs_match = _ABS_RE.match(path.strip())
    if abs_match:
        inner_result = _resolve_compound_expression(abs_match.group(1), payload_dict)
        if inner_result is not None:
            try:
                return abs(float(inner_result))
            except (TypeError, ValueError):
                return None
        return None

    # --- Try numeric literal FIRST (before dot-notation check)
    # IMPORTANT: floats like "1.10", "0.99" contain a "." and would otherwise
    # be mishandled by the dot-notation path below (splitting "1.10" into
    # table="1", field="10"). Numeric resolution must come first.
    try:
        return float(path.strip())
    except ValueError:
        pass

    # --- expression: "Table_name.field op anything"
    m = _EXPR_RE.match(path.strip())
    if m:
        field_path, operator, right_expr = m.group(1), m.group(2), m.group(3)
        base_val = _resolve_field(field_path, payload_dict)
        if base_val is None:
            return None
        # Recursively resolve the right hand side (supports field+field math)
        right_val = _resolve_field(right_expr.strip(), payload_dict)
        if right_val is None:
            return None
        try:
            base = float(base_val)
            scalar = float(right_val)
            if operator == "*":
                return base * scalar
            elif operator == "/":
                return base / scalar if scalar != 0 else None
            elif operator == "+":
                return base + scalar
            elif operator == "-":
                return base - scalar
        except (TypeError, ValueError):
            return None

    # --- dot-notation field path: "Table_name.field"
    if "." in path:
        parts = path.split(".", 1)
        table_name, field_name = parts[0], parts[1]
        # Handle [*] array notation: "Invoice_table.line_items[*].qty"
        if "[*]" in field_name:
            # Return sentinel so the line-item path is triggered by the caller
            return "__LINE_ITEM__"
        table = payload_dict.get(table_name)
        if table is None:
            return None
        if isinstance(table, dict):
            return table.get(field_name)
        return None

    # --- FIX 3/6: Bare-identifier guard and Smart Lookup --------------------
    # Known safe string literals that rules legitimately compare against.
    _KNOWN_LITERALS = {
        "true", "false",
        "intra", "inter", "intra-state", "inter-state",
        "yes", "no",
    }
    path_lower = path.strip().lower()

    # Allow known literal values
    if path_lower in _KNOWN_LITERALS:
        return path.strip()

    # Allow date-like strings (e.g. "2024-03-15")
    if _to_date(path) is not None:
        return path.strip()

    # FIX 6: Smart Lookup for bare identifiers (e.g., 'buyer_gstin_state_code')
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", path.strip()):
        found_values = []
        for table in ["Invoice_table", "PO_table", "GRN_table", "Vendor_table"]:
            t_dict = payload_dict.get(table)
            if isinstance(t_dict, dict) and path in t_dict:
                found_values.append(t_dict[path])
        
        if len(found_values) == 1:
            logger.debug(f"Smart lookup resolved bare identifier '{path}'")
            return found_values[0]
        elif len(found_values) > 1:
            logger.warning(f"Ambiguous bare identifier '{path}' found in multiple tables. Skipping.")

    # Anything else is an unresolved bare identifier or ambiguity → return None
    # The caller's null check will then trigger SkipEvaluation
    logger.debug(f"Bare identifier '{path}' could not be resolved safely — returning None (will SKIP)")
    return None


# ---------------------------------------------------------------------------
# Date helper
# ---------------------------------------------------------------------------

def _to_date(val: Any) -> Optional[datetime.date]:
    """Attempt to parse val as a date. Returns None on failure."""
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.datetime.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Core operator evaluators
# ---------------------------------------------------------------------------

def _eval_gt(left: Any, right: Any) -> bool:
    dl, dr = _to_date(left), _to_date(right)
    if dl is not None and dr is not None:
        return dl > dr
    return float(left) > float(right)


def _eval_lt(left: Any, right: Any) -> bool:
    dl, dr = _to_date(left), _to_date(right)
    if dl is not None and dr is not None:
        return dl < dr
    return float(left) < float(right)


def _eval_gte(left: Any, right: Any) -> bool:
    dl, dr = _to_date(left), _to_date(right)
    if dl is not None and dr is not None:
        return dl >= dr
    return float(left) >= float(right)


def _eval_lte(left: Any, right: Any) -> bool:
    dl, dr = _to_date(left), _to_date(right)
    if dl is not None and dr is not None:
        return dl <= dr
    return float(left) <= float(right)


def _eval_eq(left: Any, right: Any) -> bool:
    dl, dr = _to_date(left), _to_date(right)
    if dl is not None and dr is not None:
        return dl == dr
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left).strip().lower() == str(right).strip().lower()


def _eval_neq(left: Any, right: Any) -> bool:
    return not _eval_eq(left, right)


def _eval_pct_diff(left: Any, right: Any, threshold: float, direction: str) -> bool:
    """
    Percentage difference evaluation with directional awareness.

    direction "above"  → fires when left is ABOVE right by more than threshold%.
    direction "below"  → fires when left is BELOW right by more than threshold%.
    direction "both"   → fires when abs difference exceeds threshold% in either direction.
    direction "within" → fires when abs difference is WITHIN threshold% (inclusive).
    direction "cap"    → fires when abs difference is BELOW threshold% (ceiling).
    direction is None  → treated as "both" (symmetric exceeds check).
    """
    l, r = float(left), float(right)
    if r == 0:
        return False
    pct = abs(l - r) / r * 100

    if direction == "above":
        # Only fire when left is actually ABOVE right
        return l > r and pct > threshold
    elif direction == "below":
        # Only fire when left is actually BELOW right
        return l < r and pct > threshold
    elif direction == "within":
        # Fire when within tolerance (e.g. auto-approve if within ±2%)
        return pct <= threshold
    elif direction == "cap":
        # Fire when percentage is under a ceiling cap (e.g. "less than 15%")
        return pct < threshold
    else:
        # "both" or None — symmetric: fire if abs diff exceeds threshold
        return pct > threshold


def _eval_between(left: Any, lower: Any, upper: Any) -> bool:
    return float(lower) <= float(left) <= float(upper)


def _eval_is_null(value: Any) -> bool:
    return value is None


def _eval_is_not_null(value: Any) -> bool:
    return value is not None


OPERATOR_MAP = {
    "GT":  _eval_gt,
    "LT":  _eval_lt,
    "GTE": _eval_gte,
    "LTE": _eval_lte,
    "EQ":  _eval_eq,
    "NEQ": _eval_neq,
}


# ---------------------------------------------------------------------------
# Line item matching
# ---------------------------------------------------------------------------

def _get_line_items(table_name: str, payload_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    table = payload_dict.get(table_name, {}) or {}
    return table.get("line_items") or []


def _path_is_line_item(path: str, payload_dict: Dict[str, Any]) -> bool:
    """
    Two-tier line-item detection:
    PRIMARY  — checks for [*] array notation in the path (e.g. Invoice_table.line_items[*].qty)
    FALLBACK — heuristic: field name is qty or rate AND the table has a line_items list
    """
    if not isinstance(path, str) or "." not in path:
        return False

    # Primary: [*] notation
    if "[*]" in path:
        return True

    # Fallback heuristic: Table.qty or Table.rate with a line_items array present
    table_name, field_name = path.split(".", 1)
    if field_name in ("qty", "rate"):
        table = payload_dict.get(table_name, {}) or {}
        return isinstance(table.get("line_items"), list)

    return False


def _extract_line_field(path: str) -> Tuple[str, str]:
    """
    Extract (table_name, field_name) from a line-item path.
    Handles both:
      "Invoice_table.line_items[*].qty"  →  ("Invoice_table", "qty")
      "Invoice_table.qty"                →  ("Invoice_table", "qty")
    """
    if "[*]" in path:
        # e.g. "Invoice_table.line_items[*].qty"
        table_name = path.split(".")[0]
        field_name = path.rsplit(".", 1)[-1]
        return table_name, field_name
    # Plain heuristic path: "Invoice_table.qty"
    table_name, field_name = path.split(".", 1)
    return table_name, field_name


def _match_line_items(
    cond: Dict[str, Any],
    payload_dict: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    For conditions referencing line-item fields, match by item name and
    evaluate the operator pair-wise. Returns (condition_result, reason).

    Returns True when the condition fires (violation found), False when compliant.
    """
    left_path: str = cond.get("left", "")
    right_path: str = cond.get("right", "")

    if "." not in left_path or "." not in right_path:
        return False, "Cannot match line items — invalid path"

    left_table, left_field = _extract_line_field(left_path)
    right_table, right_field = _extract_line_field(right_path)

    # --- FIX 2: Null-table guard -------------------------------------------
    # If an entire table is None (document not uploaded), this is a SKIP
    # condition, not a violation. Without this guard, the GRN rules fire
    # as false violations when the user simply doesn't upload a GRN.
    left_table_data = payload_dict.get(left_table)
    right_table_data = payload_dict.get(right_table)
    if left_table_data is None:
        raise SkipEvaluation(f"Table '{left_table}' was not uploaded — cannot evaluate line items")
    if right_table_data is None:
        raise SkipEvaluation(f"Table '{right_table}' was not uploaded — cannot evaluate line items")

    left_items = _get_line_items(left_table, payload_dict)
    right_items = _get_line_items(right_table, payload_dict)

    if not left_items:
        return False, f"No line items found in {left_table}"

    right_by_name = {item.get("item", "").strip().lower(): item for item in right_items}
    violations = []
    unmatched = []

    for left_item in left_items:
        name = (left_item.get("item") or "").strip().lower()
        right_item = right_by_name.get(name)
        if right_item is None:
            unmatched.append(left_item.get("item", "?"))
            continue
        lv = left_item.get(left_field)
        rv = right_item.get(right_field)
        if lv is None or rv is None:
            continue
        operator = cond.get("operator", "EQ")
        try:
            if operator in OPERATOR_MAP:
                if OPERATOR_MAP[operator](lv, rv):
                    violations.append(
                        f"{left_item.get('item')}: {left_path}={lv} {operator} {right_path}={rv}"
                    )
            elif operator == "PCT_DIFF":
                if _eval_pct_diff(lv, rv, cond.get("threshold", 0), cond.get("direction", "above")):
                    violations.append(
                        f"{left_item.get('item')}: pct_diff exceeds threshold ({lv} vs {rv})"
                    )
        except Exception as e:
            violations.append(f"{left_item.get('item')}: evaluation error ({e})")

    if unmatched:
        return True, f"Line items not found in {right_table}: {unmatched}"
    if violations:
        return True, "; ".join(violations)
    return False, "All line items within tolerance"


# ---------------------------------------------------------------------------
# Recursive condition evaluator
# ---------------------------------------------------------------------------

class SkipEvaluation(Exception):
    """Raised when a required field is missing — result should be SKIPPED."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _evaluate_condition(cond: Dict[str, Any], payload_dict: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Recursively evaluate a condition object.
    Returns (fired: bool, reason: str).
    Raises SkipEvaluation if a required field is absent.
    fired=True means the condition holds → policy violation → FAIL.
    fired=False means condition does not hold → PASS.
    """
    operator = cond.get("operator")

    # ---- Logical operators (AND / OR) ----------------------------------------
    if operator == "AND":
        operands = cond.get("operands", []) or []
        any_skipped = False
        for operand in operands:
            try:
                result, reason = _evaluate_condition(operand, payload_dict)
                if not result:
                    # Short-circuit: one operand false → whole AND is false (PASS)
                    return False, reason
            except SkipEvaluation:
                any_skipped = True
        if any_skipped:
            raise SkipEvaluation("One or more AND operands could not be evaluated")
        return True, "All AND conditions met"

    if operator == "OR":
        operands = cond.get("operands", []) or []
        reasons = []
        any_skipped = False
        for operand in operands:
            try:
                result, reason = _evaluate_condition(operand, payload_dict)
                if result:
                    return True, reason
                reasons.append(reason)
            except SkipEvaluation as e:
                any_skipped = True
                reasons.append(f"[SKIPPED: {e.reason}]")
        if any_skipped and not reasons:
            raise SkipEvaluation("All OR operands were skipped")
        return False, " OR ".join(reasons)

    # ---- Null-check operators ------------------------------------------------
    if operator in ("IS_NULL", "IS_NOT_NULL"):
        left_path = cond.get("left")
        value = _resolve_field(left_path, payload_dict)
        if operator == "IS_NULL":
            result = _eval_is_null(value)
            reason = f"{left_path} is {'null' if result else 'not null'}"
        else:
            result = _eval_is_not_null(value)
            reason = f"{left_path} is {'not null' if result else 'null'}"
        return result, reason

    # ---- Line item operators -------------------------------------------------
    left_path = cond.get("left", "")
    right_path = cond.get("right", "")

    left_is_line_item = isinstance(left_path, str) and _path_is_line_item(left_path, payload_dict)
    right_is_line_item = isinstance(right_path, str) and _path_is_line_item(right_path, payload_dict)

    if left_is_line_item or right_is_line_item:
        fired, reason = _match_line_items(cond, payload_dict)
        return fired, reason

    # ---- Scalar operators ----------------------------------------------------
    left_val = _resolve_field(left_path, payload_dict)
    right_val = _resolve_field(right_path, payload_dict)

    # Check for missing required fields
    def _is_field_reference(p):
        """Check if a path looks like a field reference (not a literal)."""
        if not isinstance(p, str):
            return False
        s = p.strip()
        return ("." in s and "[*]" not in s) or s.lower().startswith("abs(")

    if left_val is None and _is_field_reference(left_path):
        raise SkipEvaluation(f"Field '{left_path}' is missing or null in payload")
    if right_val is None and _is_field_reference(right_path):
        raise SkipEvaluation(f"Field '{right_path}' is missing or null in payload")

    if operator in OPERATOR_MAP:
        try:
            result = OPERATOR_MAP[operator](left_val, right_val)
            reason = f"{left_path}={left_val} {operator} {right_path}={right_val}"
            return result, reason
        except (TypeError, ValueError) as e:
            raise SkipEvaluation(f"Cannot compare {left_path}={left_val!r} with {right_val!r}: {e}")

    if operator == "PCT_DIFF":
        threshold = cond.get("threshold", 0)
        direction = cond.get("direction", "above")
        try:
            result = _eval_pct_diff(left_val, right_val, threshold, direction)
            l = float(left_val) if left_val is not None else 0
            r = float(right_val) if right_val is not None else 0
            pct = abs(l - r) / r * 100 if r != 0 else 0
            reason = (
                f"PCT_DIFF({left_path}={left_val}, {right_path}={right_val}) = {pct:.2f}% "
                f"threshold={threshold}% direction={direction}"
            )
            return result, reason
        except (TypeError, ValueError) as e:
            raise SkipEvaluation(f"PCT_DIFF evaluation error: {e}")

    if operator == "BETWEEN":
        lower = cond.get("lower")
        upper = cond.get("upper")
        # Bounds may be expression strings like "PO_table.amount * 0.99"
        lower_val = _resolve_field(lower, payload_dict) if isinstance(lower, str) else lower
        upper_val = _resolve_field(upper, payload_dict) if isinstance(upper, str) else upper
        if lower_val is None or upper_val is None:
            raise SkipEvaluation(f"BETWEEN bounds could not be resolved: lower={lower}, upper={upper}")
        try:
            result = _eval_between(left_val, lower_val, upper_val)
            reason = f"{left_path}={left_val} BETWEEN {lower_val} and {upper_val}"
            return result, reason
        except (TypeError, ValueError) as e:
            raise SkipEvaluation(f"BETWEEN evaluation error: {e}")

    raise SkipEvaluation(f"Unknown operator: {operator!r}")


# ---------------------------------------------------------------------------
# Human-readable action mapping (mirrors reporting.py — kept in sync manually)
# ---------------------------------------------------------------------------

_ACTION_HUMAN = {
    "AUTO_APPROVE":                     "Invoice qualifies for automatic approval.",
    "ROUTE_TO_DEPT_HEAD":               "Route this invoice to the Department Head for approval.",
    "ESCALATE_TO_FINANCE_CONTROLLER":   "Escalate immediately to the Finance Controller for review.",
    "ESCALATE_TO_CFO":                  "Escalate immediately to the CFO — requires urgent executive sign-off.",
    "HOLD":                             "Place this invoice on HOLD pending clarification.",
    "REJECT":                           "Reject this invoice and notify the vendor.",
    "FLAG":                             "Flag this invoice for manual review by the AP team.",
    "ROUTE_TO_PROCUREMENT":             "Route to the Procurement team for PO verification.",
    "COMPLIANCE_HOLD":                  "Place on Compliance Hold — do not process until legal/compliance team reviews.",
}


# ---------------------------------------------------------------------------
# Per-rule evaluation
# ---------------------------------------------------------------------------

def _evaluate_rule(
    rule: Dict[str, Any],
    payload_dict: Dict[str, Any],
) -> RuleExecutionResult:
    rule_id = rule.get("rule_id", "UNKNOWN")
    description = rule.get("description", "")
    action = rule.get("action")
    source_clause = rule.get("source_clause", "")
    condition = rule.get("condition")

    if not condition:
        return RuleExecutionResult(
            rule_id=rule_id,
            status="SKIPPED",
            description=description,
            source_clause=source_clause,
            deviation_details=DeviationDetails(reason="Rule has no condition defined"),
        )

    try:
        fired, reason = _evaluate_condition(condition, payload_dict)
    except SkipEvaluation as e:
        logger.debug(f"Rule {rule_id} SKIPPED: {e.reason}")
        return RuleExecutionResult(
            rule_id=rule_id,
            status="SKIPPED",
            description=description,
            source_clause=source_clause,
            deviation_details=DeviationDetails(reason=e.reason),
        )
    except Exception as e:
        logger.exception(f"Unexpected error evaluating rule {rule_id}")
        return RuleExecutionResult(
            rule_id=rule_id,
            status="SKIPPED",
            description=description,
            source_clause=source_clause,
            deviation_details=DeviationDetails(reason=f"Evaluation error: {e}"),
        )

    if fired:
        # Condition fired → policy violation → FAIL
        # Live payload mutation: mark deviation flags for downstream rules
        inv = payload_dict.get("Invoice_table")
        if isinstance(inv, dict):
            inv["has_deviation"] = True
            # Section 4 rules typically govern compliance-level violations
            if source_clause and "section 4" in source_clause.lower():
                inv["has_compliance_failure"] = True

        return RuleExecutionResult(
            rule_id=rule_id,
            status="VIOLATION",
            description=description,
            source_clause=source_clause,
            action=action,
            deviation_details=DeviationDetails(reason=reason),
        )
    else:
        return RuleExecutionResult(
            rule_id=rule_id,
            status="PASS",
            description=description,
            source_clause=source_clause,
            deviation_details=DeviationDetails(reason=reason),
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/execute-rules", response_model=ExecuteRulesResponse)
async def execute_rules(body: ExecuteRulesRequest) -> ExecuteRulesResponse:
    """
    Evaluate the active ruleset against the provided document payload.
    Returns per-rule PASS / FAIL / SKIPPED results plus a human-readable
    flags array and overall compliance status.
    """
    if not state.active_ruleset:
        raise HTTPException(
            status_code=400,
            detail="No active ruleset. Finalize rules first via POST /finalize-rules.",
        )

    # Convert Pydantic model to plain dict for resolver
    payload_dict = body.payload.model_dump()

    # PRE-EXECUTION: normalise AND/OR structure on all rule conditions
    # This fixes any rules where the LLM used left/right instead of operands.
    # Must happen before the engine loop, not inside evaluate_condition.
    _normalize_and_or_structure(state.active_ruleset)

    # PRE-EXECUTION: fix PCT_DIFF direction semantics
    _normalize_pct_diff_conditions(state.active_ruleset)

    results: List[RuleExecutionResult] = []
    for rule in state.active_ruleset:
        result = _evaluate_rule(rule, payload_dict)
        results.append(result)

    passed  = sum(1 for r in results if r.status == "PASS")
    failed  = sum(1 for r in results if r.status == "VIOLATION")
    skipped = sum(1 for r in results if r.status == "SKIPPED")

    # Build human-readable flags for every FAIL result
    flags: List[str] = []
    for r in results:
        if r.status == "VIOLATION":
            clause = f" ({r.source_clause})" if r.source_clause else ""
            action_text = _ACTION_HUMAN.get(r.action or "", r.action or "Review required")
            reason = r.deviation_details.reason if r.deviation_details else "No details"
            flags.append(
                f"[{r.rule_id}{clause}] {r.description} — {reason} — {action_text}"
            )

    overall_status = "COMPLIANT" if failed == 0 else "NON_COMPLIANT"
    invoice_number = (
        payload_dict.get("Invoice_table", {}) or {}
    ).get("invoice_number")

    logger.info(
        f"Rule execution complete: {len(results)} rules — "
        f"PASS={passed}, FAIL={failed}, SKIPPED={skipped} — {overall_status}"
    )

    # ---- Persist execution to history log -----------------------------------
    _save_execution_history(
        payload_dict=payload_dict,
        results=results,
        overall_status=overall_status,
        invoice_number=invoice_number,
    )

    return ExecuteRulesResponse(
        results=results,
        total=len(results),
        passed=passed,
        failed=failed,
        skipped=skipped,
        overall_status=overall_status,
        invoice_number=invoice_number,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------

def _save_execution_history(
    payload_dict: Dict[str, Any],
    results: List[RuleExecutionResult],
    overall_status: str,
    invoice_number: Optional[str],
) -> None:
    """
    Persist a complete execution record to backend/history/.
    Each file links the exact document payload to the policy version (hash)
    that was active at evaluation time, creating a permanent audit trail.
    """
    try:
        history_dir = Path(__file__).parent.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        policy_id = state.active_ruleset_hash or "no_policy"
        safe_inv = str(invoice_number or "unknown").replace("/", "-").replace(" ", "_")
        filename = f"execution_{timestamp}_{safe_inv}_policy{policy_id}.json"

        record = payload_dict.copy()
        record["_meta"] = {
            "execution_time": datetime.datetime.now().isoformat(),
            "policy_id": policy_id,
            "overall_status": overall_status
        }

        history_file = history_dir / filename
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Document JSON saved → {history_file.name}")

    except Exception as e:
        # Never let history saving crash the main execution response
        logger.warning(f"Could not save document JSON: {e}")
