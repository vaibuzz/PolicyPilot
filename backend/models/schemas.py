"""
Pydantic schemas for all PolicyPilot API inputs and outputs.
All modules import from here — do not duplicate model definitions elsewhere.
"""

from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Rule condition tree (recursive)
# ---------------------------------------------------------------------------

OperatorType = Literal[
    "GT", "LT", "GTE", "LTE", "EQ", "NEQ",
    "PCT_DIFF", "BETWEEN", "AND", "OR",
    "IS_NULL", "IS_NOT_NULL",
]

# Known action strings supported by the Rule Engine.
# Used for warning/logging only — Rule.action accepts any str so the LLM
# never crashes the pipeline with an unknown action.
KNOWN_ACTIONS = {
    "AUTO_APPROVE",
    "ROUTE_TO_AP_CLERK",
    "ROUTE_TO_DEPT_HEAD",
    "ESCALATE_TO_FINANCE_CONTROLLER",
    "ESCALATE_TO_CFO",
    "HOLD",
    "REJECT",
    "FLAG",
    "ROUTE_TO_PROCUREMENT",
    "COMPLIANCE_HOLD",
}

ReviewStatusType = Literal["pending", "accepted", "modified", "kept_original"]


class RuleCondition(BaseModel):
    operator: OperatorType
    # Leaf-node fields (used by non-AND/OR operators)
    left: Optional[Union[str, float]] = None
    right: Optional[Union[str, float]] = None
    # PCT_DIFF-specific
    threshold: Optional[float] = None
    direction: Optional[Literal["above", "below"]] = None
    # BETWEEN-specific
    lower: Optional[Union[str, float]] = None
    upper: Optional[Union[str, float]] = None
    # AND/OR operands
    operands: Optional[List["RuleCondition"]] = None

    model_config = {"extra": "allow"}


RuleCondition.model_rebuild()


class NotificationConfig(BaseModel):
    type: Literal["email"] = "email"
    to: List[str]
    within_minutes: int


class Rule(BaseModel):
    rule_id: str
    source_clause: str
    description: str
    condition: RuleCondition
    action: str
    requires_justification: bool = False
    notification: Optional[NotificationConfig] = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    raw_text: str
    conflict_with: List[str] = Field(default_factory=list)
    suggested_fix: Optional[Dict[str, Any]] = None
    review_status: ReviewStatusType = "pending"
    section: str = "Unknown"  # top-level section derived from source_clause

    model_config = {"extra": "allow"}

    @field_validator("action", mode="before")
    @classmethod
    def _uppercase_action(cls, v: Any) -> Any:
        """Safety-net: upper-case the action string at schema level.
        Primary normalisation happens in extraction._normalize_actions.
        This only catches anything that bypasses that step."""
        if isinstance(v, str):
            return v.upper()
        return v


# ---------------------------------------------------------------------------
# Module 1 — Ingestion
# ---------------------------------------------------------------------------

class IngestionResponse(BaseModel):
    markdown: str
    filename: str
    parser_used: Literal["docling", "pymupdf", "passthrough"]


# ---------------------------------------------------------------------------
# Module 2 — Extraction + Conflict Detection
# ---------------------------------------------------------------------------

class ConflictObject(BaseModel):
    conflict_id: str
    rule_id_a: str
    rule_id_b: str
    severity: Optional[Literal["critical", "high", "medium", "low"]] = None
    explanation: str
    suggested_fix: Optional[Dict[str, Any]] = None


class ExtractionSummary(BaseModel):
    total_rules: int
    high_confidence: int    # >= 0.9
    medium_confidence: int  # 0.7 – 0.89
    low_confidence: int     # < 0.7
    conflicts_found: int


class ExtractionResponse(BaseModel):
    rules: List[Rule]
    conflicts: List[ConflictObject]
    summary: ExtractionSummary


# ---------------------------------------------------------------------------
# Module 3 — Finalization
# ---------------------------------------------------------------------------

class FinalizeRulesRequest(BaseModel):
    rules: List[Rule]


class FinalizeRulesResponse(BaseModel):
    accepted: int
    modified: int
    kept_original: int
    total: int
    message: str


# ---------------------------------------------------------------------------
# Module 4 — Rule Engine
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    item: Optional[str] = None
    qty: Optional[float] = None
    rate: Optional[float] = None

    model_config = {"extra": "allow"}


class InvoiceTable(BaseModel):
    invoice_number: Optional[str] = None
    date: Optional[str] = None
    grand_total: Optional[float] = None
    taxable_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    cgst: Optional[float] = None
    sgst: Optional[float] = None
    igst: Optional[float] = None
    supply_type: Optional[str] = None
    place_of_supply: Optional[str] = None
    po_number: Optional[str] = None
    gstin: Optional[str] = None
    has_deviation: Optional[bool] = None
    has_compliance_failure: Optional[bool] = None
    tax_calculation_error: Optional[float] = None
    line_items: Optional[List[LineItem]] = None

    model_config = {"extra": "allow"}


class POTable(BaseModel):
    po_number: Optional[str] = None
    amount: Optional[float] = None
    grand_total: Optional[float] = None
    date: Optional[str] = None
    line_items: Optional[List[LineItem]] = None

    model_config = {"extra": "allow"}


class GRNLineItem(BaseModel):
    item: str
    qty: Optional[float] = None

    model_config = {"extra": "allow"}


class GRNTable(BaseModel):
    grn_number: Optional[str] = None
    date: Optional[str] = None
    po_number: Optional[str] = None
    line_items: Optional[List[GRNLineItem]] = None

    model_config = {"extra": "allow"}


class VendorTable(BaseModel):
    gstin: Optional[str] = None
    pan: Optional[str] = None
    watchlist: Optional[bool] = None

    model_config = {"extra": "allow"}


class DocumentPayload(BaseModel):
    Invoice_table: Optional[InvoiceTable] = None
    PO_table: Optional[POTable] = None
    GRN_table: Optional[GRNTable] = None
    Vendor_table: Optional[VendorTable] = None

    model_config = {"extra": "allow"}


class DeviationDetails(BaseModel):
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    reason: str


class RuleExecutionResult(BaseModel):
    rule_id: str
    status: Literal["PASS", "VIOLATION", "SKIPPED"]
    description: str
    source_clause: Optional[str] = None
    action: Optional[str] = None
    deviation_details: Optional[DeviationDetails] = None


class ExecuteRulesRequest(BaseModel):
    payload: DocumentPayload


class ExecuteRulesResponse(BaseModel):
    results: List[RuleExecutionResult]
    total: int
    passed: int
    failed: int
    skipped: int
    overall_status: Literal["COMPLIANT", "NON_COMPLIANT"] = "COMPLIANT"
    invoice_number: Optional[str] = None
    flags: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Module 5 — Document Extraction
# ---------------------------------------------------------------------------

class ExtractDocumentsResponse(BaseModel):
    payload: DocumentPayload
    field_vocabulary: List[str]
    documents_received: List[str]


# ---------------------------------------------------------------------------
# Module 6 — Reporting
# ---------------------------------------------------------------------------

class ReportDetail(BaseModel):
    rule_id: str
    source_clause: str
    description: str
    status: Literal["PASS", "VIOLATION", "SKIPPED"]
    action: Optional[str] = None
    deviation_details: Optional[DeviationDetails] = None


class Recommendation(BaseModel):
    rule_id: str
    action_human: str
    urgency: Literal["critical", "high", "medium", "low"]


class ReportSummary(BaseModel):
    total_rules_evaluated: int
    passed: int
    failed: int
    skipped: int
    overall_status: Literal["COMPLIANT", "NON_COMPLIANT"]


class Report(BaseModel):
    summary: ReportSummary
    details: List[ReportDetail]
    recommendations: List[Recommendation]
    invoice_number: Optional[str] = None


class SendReportRequest(BaseModel):
    execution_results: List[RuleExecutionResult]
    email: str
    invoice_number: Optional[str] = None


class SendReportResponse(BaseModel):
    report: Report
    delivery_method: Literal["sendgrid", "console_log"]
    message: str
