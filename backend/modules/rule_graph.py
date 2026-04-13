"""
Module 7 — Visual Rule Graph Generator
POST /generate-rule-graph

Zero LLM calls. Pure Python.
Reads any rules array and produces a Mermaid flowchart TD string.
"""

import logging
import re
import textwrap
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class RuleGraphRequest(BaseModel):
    rules: List[Dict[str, Any]]


class RuleGraphResponse(BaseModel):
    mermaid: str


# ---------------------------------------------------------------------------
# Action → terminal node colour class mapping
# ---------------------------------------------------------------------------

ACTION_CLASS_MAP = {
    "REJECT":                         "red",
    "AUTO_APPROVE":                   "green",
    "ROUTE_TO_DEPT_HEAD":             "blue",
    "ESCALATE_TO_FINANCE_CONTROLLER": "amber",
    "ESCALATE_TO_CFO":                "amber",
    "HOLD":                           "orange",
    "FLAG":                           "orange",
    "ROUTE_TO_AP_CLERK":              "blue",
    "ROUTE_TO_PROCUREMENT":           "blue",
    "COMPLIANCE_HOLD":                "orange",
}

ACTION_LABEL_MAP = {
    "REJECT":                         "❌ Reject Invoice",
    "AUTO_APPROVE":                   "✅ Auto Approve",
    "ROUTE_TO_DEPT_HEAD":             "→ Route to Dept Head",
    "ESCALATE_TO_FINANCE_CONTROLLER": "→ Escalate: Finance Controller",
    "ESCALATE_TO_CFO":                "→ Escalate: CFO",
    "HOLD":                           "⏸ Hold for Review",
    "FLAG":                           "⚑ Flag for Review",
    "ROUTE_TO_AP_CLERK":              "→ Route to AP Clerk",
    "ROUTE_TO_PROCUREMENT":           "→ Route to Procurement",
    "COMPLIANCE_HOLD":                "⚠ Compliance Hold",
}


def _sanitize_id(text: str) -> str:
    """Turn a rule_id into a safe Mermaid node ID (alphanumeric + underscores only)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", text)


def _wrap_text(text: str, max_line_len: int = 45, max_chars: int = 140) -> str:
    """Wrap text separated by <br/>, and truncate safely if it exceeds max_chars."""
    if not text:
        return "No description"
    
    text = text.strip()
    
    # Truncate if too long to prevent box overflow/bounding box renderer bugs
    if len(text) > max_chars:
        # cut at max_chars, then rsplit by space to not cut mid-word
        cut_text = text[:max_chars].rsplit(' ', 1)[0]
        text = cut_text + "..."
        
    return "<br/>".join(textwrap.wrap(text, width=max_line_len))


def _escape_mermaid_label(text: str) -> str:
    """Escape characters that break Mermaid node labels."""
    return (
        text
        .replace('"', "'")
        .replace("{", "(")
        .replace("}", ")")
        .replace("[", "(")
        .replace("]", ")")
        .replace(">=", "≥")
        .replace("<=", "≤")
        .replace(">", "›")
        .replace("<", "‹")
        .replace("&", "and")
        .replace("|", "-")
        .replace(";", ",")
    )


def generate_mermaid_from_rules(rules: List[Dict[str, Any]]) -> str:
    """
    Build a Mermaid flowchart TD string from any rules array.
    Zero hardcoded rule IDs. Works generically on whatever rules are present.
    """
    if not rules:
        return "flowchart TD\n    START([No rules loaded])"

    lines = ["flowchart TD"]

    # ── START node ──────────────────────────────────────────────────────────
    lines.append("    START([Invoice received])")
    lines.append("")

    # ── Define shared terminal action nodes ─────────────────────────────────
    used_actions = set(str(r.get("action", "FLAG")).upper() for r in rules)
    for action in used_actions:
        terminal_id = f"TERM_{action}"
        terminal_label = _escape_mermaid_label(ACTION_LABEL_MAP.get(action, action))
        cls = ACTION_CLASS_MAP.get(action, "orange")
        lines.append(f"    {terminal_id}[{terminal_label}]:::{cls}")
    lines.append("")

    prev_decision_id = "START"

    for idx, rule in enumerate(rules):
        rule_id_raw = rule.get("rule_id", f"RULE_{idx}")
        description = rule.get("description", "")
        source_clause = rule.get("source_clause", "")
        action = str(rule.get("action", "FLAG")).upper()
        conflict_with = rule.get("conflict_with", []) or []

        # ── Decision node ─────────────────────────────────────────────────
        node_id      = _sanitize_id(rule_id_raw)
        node_desc    = _escape_mermaid_label(_wrap_text(description, 45))
        node_clause  = _escape_mermaid_label(source_clause) if source_clause else ""

        if node_clause:
            node_label = f'"{node_desc}<br/><small>{node_clause}</small>"'
        else:
            node_label = f'"{node_desc}"'

        lines.append(f"    {node_id}({node_label})")

        # ── Edge from previous node → this decision ───────────────────────
        if idx == 0:
            lines.append(f"    START --> {node_id}")
        else:
            lines.append(f"    {prev_decision_id} -->|No| {node_id}")

        # ── Terminal node for the YES branch ─────────────────────────────
        terminal_id = f"TERM_{action}"
        lines.append(f"    {node_id} -->|Yes| {terminal_id}")

        # ── Conflict dashed edges ─────────────────────────────────────────
        if conflict_with:
            for conflict_id in conflict_with:
                conflict_node_id_raw = conflict_id if isinstance(conflict_id, str) else str(conflict_id)
                # The conflict_with array holds rule_ids of the conflicting rule
                conflict_node_id = _sanitize_id(conflict_node_id_raw)
                lines.append(
                    f"    CONFLICT_{_sanitize_id(rule_id_raw)}[/⚠ Conflict: {_escape_mermaid_label(conflict_node_id_raw)}/] "
                    f"-. conflict .-> {node_id}"
                )

        lines.append("")
        prev_decision_id = node_id

    # ── Fallback terminal node ────────────────────────────────────────────
    lines.append("    FALLBACK([No rule matched — Manual Review])")
    lines.append(f"    {prev_decision_id} -->|No| FALLBACK")
    lines.append("")

    # ── classDef declarations ─────────────────────────────────────────────
    lines.append("    classDef red    fill:#E24B4A,color:#fff,stroke:#c0392b,stroke-width:2px")
    lines.append("    classDef green  fill:#639922,color:#fff,stroke:#4a7a1a,stroke-width:2px")
    lines.append("    classDef amber  fill:#BA7517,color:#fff,stroke:#956010,stroke-width:2px")
    lines.append("    classDef blue   fill:#185FA5,color:#fff,stroke:#0f4a84,stroke-width:2px")
    lines.append("    classDef orange fill:#D85A30,color:#fff,stroke:#b04820,stroke-width:2px")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/generate-rule-graph", response_model=RuleGraphResponse)
def generate_rule_graph(body: RuleGraphRequest) -> RuleGraphResponse:
    """
    Zero LLM calls. Pure Python graph generation.
    Returns a Mermaid flowchart TD string from the given rules array.
    """
    logger.info(f"Generating rule graph for {len(body.rules)} rules")
    mermaid_str = generate_mermaid_from_rules(body.rules)
    logger.info(f"Rule graph generated: {len(mermaid_str)} chars")
    return RuleGraphResponse(mermaid=mermaid_str)
