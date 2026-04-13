"""
Tests for Module 4 — Rule Engine

These tests inject synthetic rules directly into state.active_ruleset
and run the evaluator against each of the three test payloads.
No Claude API calls are made.

Run: cd backend && python -m pytest tests/test_rule_engine.py -v
"""

import json
import sys
import os

# Ensure backend root is on the path when running from tests/ folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import state
from modules.rule_engine import _evaluate_rule
from models.schemas import DocumentPayload


# ---------------------------------------------------------------------------
# Synthetic rules that mirror what Claude would extract from a real AP policy
# ---------------------------------------------------------------------------

RULE_AMOUNT_ESCALATION = {
    "rule_id": "AP-DEV-001",
    "source_clause": "Section 5.3",
    "description": "Invoice amount deviating more than 10% from PO amount must be escalated to Finance Controller",
    "condition": {
        "operator": "PCT_DIFF",
        "left": "Invoice_table.grand_total",
        "right": "PO_table.amount",
        "threshold": 10,
        "direction": "above",
    },
    "action": "ESCALATE_TO_FINANCE_CONTROLLER",
    "requires_justification": True,
    "notification": None,
    "confidence_score": 0.97,
    "raw_text": "Any invoice amount exceeding the PO value by more than 10% must be escalated to the Finance Controller.",
    "conflict_with": [],
    "suggested_fix": None,
    "review_status": "accepted",
}

RULE_AUTO_APPROVE_SMALL = {
    "rule_id": "AP-APR-001",
    "source_clause": "Section 5.1",
    "description": "Invoices under INR 1,00,000 from non-watchlist vendors are auto-approved",
    "condition": {
        "operator": "AND",
        "operands": [
            {
                "operator": "LT",
                "left": "Invoice_table.grand_total",
                "right": 100000,
            },
            {
                "operator": "EQ",
                "left": "Vendor_table.watchlist",
                "right": False,
            },
        ],
    },
    "action": "AUTO_APPROVE",
    "requires_justification": False,
    "notification": None,
    "confidence_score": 0.95,
    "raw_text": "Invoices below INR 1,00,000 shall be auto-approved provided the vendor is not listed on the watchlist.",
    "conflict_with": [],
    "suggested_fix": None,
    "review_status": "accepted",
}

RULE_LINE_ITEM_QTY = {
    "rule_id": "AP-TWM-001",
    "source_clause": "Section 2.2(a)",
    "description": "Invoice line item quantity must match PO line item quantity exactly",
    "condition": {
        "operator": "EQ",
        "left": "Invoice_table.qty",
        "right": "PO_table.qty",
    },
    "action": "HOLD",
    "requires_justification": True,
    "notification": None,
    "confidence_score": 0.96,
    "raw_text": "Quantities on the invoice must exactly match those authorised on the purchase order.",
    "conflict_with": [],
    "suggested_fix": None,
    "review_status": "accepted",
}

RULE_INTRA_STATE_TAX = {
    "rule_id": "AP-TAX-001",
    "source_clause": "Section 3.1(b)",
    "description": "For intra-state supply, CGST must equal SGST",
    "condition": {
        "operator": "EQ",
        "left": "Invoice_table.cgst",
        "right": "Invoice_table.sgst",
    },
    "action": "FLAG",
    "requires_justification": False,
    "notification": None,
    "confidence_score": 0.98,
    "raw_text": "For intra-state transactions CGST and SGST amounts must be equal.",
    "conflict_with": [],
    "suggested_fix": None,
    "review_status": "accepted",
}


def _load_payload(filename: str) -> DocumentPayload:
    path = os.path.join(os.path.dirname(__file__), "test_payloads", filename)
    with open(path) as f:
        data = json.load(f)
    # Strip _comment keys before parsing
    data.pop("_comment", None)
    return DocumentPayload.model_validate(data)


# ---------------------------------------------------------------------------
# Fixture: inject synthetic ruleset before each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def inject_ruleset():
    state.active_ruleset.clear()
    state.active_ruleset.extend([
        RULE_AMOUNT_ESCALATION,
        RULE_AUTO_APPROVE_SMALL,
        RULE_LINE_ITEM_QTY,
        RULE_INTRA_STATE_TAX,
    ])
    yield
    state.active_ruleset.clear()


# ---------------------------------------------------------------------------
# Payload 1 — All rules pass
# ---------------------------------------------------------------------------

class TestPayloadPass:
    def test_amount_escalation_passes(self):
        """98000 vs 100000 = 2% deviation — below 10% threshold — should PASS"""
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(RULE_AMOUNT_ESCALATION, payload.model_dump())
        assert result.status == "PASS", f"Expected PASS: {result.deviation_details}"

    def test_auto_approve_fires(self):
        """98000 < 100000, not watchlisted → AUTO_APPROVE condition fires → FAIL (rule triggers)"""
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(RULE_AUTO_APPROVE_SMALL, payload.model_dump())
        # When the AUTO_APPROVE rule fires it means the invoice qualifies for approval
        # The engine records this as FAIL (condition fired) — the action AUTO_APPROVE is the outcome
        assert result.status == "FAIL"
        assert result.action == "AUTO_APPROVE"

    def test_line_items_match(self):
        """Qty matches in both tables — should PASS"""
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(RULE_LINE_ITEM_QTY, payload.model_dump())
        assert result.status == "PASS", f"Expected PASS: {result.deviation_details}"

    def test_cgst_equals_sgst(self):
        """7474.5 == 7474.5 — should fire (FAIL with FLAG)"""
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(RULE_INTRA_STATE_TAX, payload.model_dump())
        assert result.status == "FAIL"
        assert result.action == "FLAG"


# ---------------------------------------------------------------------------
# Payload 2 — Amount escalation (15% deviation)
# ---------------------------------------------------------------------------

class TestPayloadEscalation:
    def test_escalation_triggers(self):
        """115000 vs 100000 = 15% > 10% threshold → FAIL with ESCALATE_TO_FINANCE_CONTROLLER"""
        payload = _load_payload("payload_escalation.json")
        result = _evaluate_rule(RULE_AMOUNT_ESCALATION, payload.model_dump())
        assert result.status == "FAIL", f"Expected FAIL but got: {result.status}"
        assert result.action == "ESCALATE_TO_FINANCE_CONTROLLER"

    def test_pct_diff_value(self):
        """Verify the pct calculation: abs(115000-100000)/100000*100 = 15%"""
        from modules.rule_engine import _eval_pct_diff
        result = _eval_pct_diff(115000, 100000, 10, "above")
        assert result is True

    def test_below_threshold_does_not_trigger(self):
        """A 5% deviation should not trigger the escalation"""
        from modules.rule_engine import _eval_pct_diff
        result = _eval_pct_diff(105000, 100000, 10, "above")
        assert result is False


# ---------------------------------------------------------------------------
# Payload 3 — Line item quantity mismatch (12 vs 10)
# ---------------------------------------------------------------------------

class TestPayloadLineMismatch:
    def test_line_item_mismatch_fails(self):
        """Invoice qty=12, PO qty=10 — mismatch → FAIL with HOLD"""
        payload = _load_payload("payload_line_mismatch.json")
        result = _evaluate_rule(RULE_LINE_ITEM_QTY, payload.model_dump())
        assert result.status == "FAIL", f"Expected FAIL: {result.deviation_details}"
        assert result.action == "HOLD"

    def test_deviation_details_present(self):
        """deviation_details.reason should mention the mismatch item"""
        payload = _load_payload("payload_line_mismatch.json")
        result = _evaluate_rule(RULE_LINE_ITEM_QTY, payload.model_dump())
        assert result.deviation_details is not None
        assert result.deviation_details.reason  # not empty


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_skipped_when_field_missing(self):
        """When a required field is absent from payload, status is SKIPPED not a crash"""
        rule = {
            "rule_id": "AP-TEST-SKIP",
            "description": "Test skip on missing field",
            "condition": {
                "operator": "GT",
                "left": "Invoice_table.grand_total",
                "right": "GRN_table.some_nonexistent_field",
            },
            "action": "FLAG",
            "confidence_score": 0.9,
            "conflict_with": [],
            "suggested_fix": None,
            "review_status": "accepted",
        }
        empty_payload = DocumentPayload(
            Invoice_table=None,
            PO_table=None,
            GRN_table=None,
            Vendor_table=None,
        )
        result = _evaluate_rule(rule, empty_payload.model_dump())
        assert result.status == "SKIPPED"
        assert result.deviation_details is not None

    def test_or_operator_short_circuits(self):
        """OR condition passes when any operand is true"""
        rule = {
            "rule_id": "AP-TEST-OR",
            "description": "Test OR operator",
            "condition": {
                "operator": "OR",
                "operands": [
                    {"operator": "GT", "left": "Invoice_table.grand_total", "right": 50000},
                    {"operator": "GT", "left": "Invoice_table.grand_total", "right": 200000},
                ],
            },
            "action": "FLAG",
            "confidence_score": 0.9,
            "conflict_with": [],
            "suggested_fix": None,
            "review_status": "accepted",
        }
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(rule, payload.model_dump())
        # 98000 > 50000 is True → OR fires → FAIL
        assert result.status == "FAIL"

    def test_between_operator(self):
        """BETWEEN operator: 98000 BETWEEN 50000 and 150000 → fires"""
        rule = {
            "rule_id": "AP-TEST-BTW",
            "description": "Test BETWEEN operator",
            "condition": {
                "operator": "BETWEEN",
                "left": "Invoice_table.grand_total",
                "lower": 50000,
                "upper": 150000,
            },
            "action": "FLAG",
            "confidence_score": 0.9,
            "conflict_with": [],
            "suggested_fix": None,
            "review_status": "accepted",
        }
        payload = _load_payload("payload_pass.json")
        result = _evaluate_rule(rule, payload.model_dump())
        assert result.status == "FAIL"  # condition fired
