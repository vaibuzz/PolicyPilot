"""
Module 6 — Report Generation and Notification
POST /send-report

Builds a structured compliance report from rule execution results and
sends it via SendGrid (if SENDGRID_API_KEY is set) or prints to stdout.
"""

import logging
import os
import json
import base64
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.schemas import (
    DeviationDetails,
    Recommendation,
    Report,
    ReportDetail,
    ReportSummary,
    RuleExecutionResult,
    SendReportRequest,
    SendReportResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Urgency mapping
# ---------------------------------------------------------------------------

_ACTION_URGENCY = {
    "ESCALATE_TO_CFO": "critical",
    "ESCALATE_TO_FINANCE_CONTROLLER": "high",
    "ROUTE_TO_DEPT_HEAD": "medium",
    "COMPLIANCE_HOLD": "medium",
    "HOLD": "low",
    "REJECT": "low",
    "FLAG": "low",
    "ROUTE_TO_PROCUREMENT": "low",
    "AUTO_APPROVE": "low",
}

_ACTION_HUMAN = {
    "AUTO_APPROVE": "Invoice qualifies for automatic approval.",
    "ROUTE_TO_DEPT_HEAD": "Route this invoice to the Department Head for approval.",
    "ESCALATE_TO_FINANCE_CONTROLLER": "Escalate immediately to the Finance Controller for review.",
    "ESCALATE_TO_CFO": "Escalate immediately to the CFO — requires urgent executive sign-off.",
    "HOLD": "Place this invoice on HOLD pending clarification.",
    "REJECT": "Reject this invoice and notify the vendor.",
    "FLAG": "Flag this invoice for manual review by the AP team.",
    "ROUTE_TO_PROCUREMENT": "Route to the Procurement team for PO verification.",
    "COMPLIANCE_HOLD": "Place on Compliance Hold — do not process until legal/compliance team reviews.",
}


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _build_report(
    results: List[RuleExecutionResult],
    invoice_number: Optional[str],
) -> Report:
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "VIOLATION")
    skipped = sum(1 for r in results if r.status == "SKIPPED")
    overall_status = "COMPLIANT" if failed == 0 else "NON_COMPLIANT"

    details: List[ReportDetail] = [
        ReportDetail(
            rule_id=r.rule_id,
            source_clause=r.source_clause or "",
            description=r.description,
            status=r.status,
            action=r.action if r.status == "VIOLATION" else None,
            deviation_details=r.deviation_details,
        )
        for r in results
    ]

    recommendations: List[Recommendation] = []
    for r in results:
        if r.status == "VIOLATION" and r.action:
            recommendations.append(
                Recommendation(
                    rule_id=r.rule_id,
                    action_human=_ACTION_HUMAN.get(r.action, r.action),
                    urgency=_ACTION_URGENCY.get(r.action, "low"),
                )
            )

    return Report(
        summary=ReportSummary(
            total_rules_evaluated=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            overall_status=overall_status,
        ),
        details=details,
        recommendations=recommendations,
        invoice_number=invoice_number,
    )


# ---------------------------------------------------------------------------
# Email formatter
# ---------------------------------------------------------------------------

def _format_email_body(report: Report, recipient: str) -> str:
    inv = report.invoice_number or "N/A"
    status = report.summary.overall_status
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "=" * 72,
        f"AP COMPLIANCE REPORT — {status} — Invoice {inv}",
        f"Generated: {ts}   Recipient: {recipient}",
        "=" * 72,
        "",
        "SUMMARY",
        "-" * 40,
        f"Total Rules Evaluated : {report.summary.total_rules_evaluated}",
        f"Passed                : {report.summary.passed}",
        f"Failed                : {report.summary.failed}",
        f"Skipped               : {report.summary.skipped}",
        f"Overall Status        : {status}",
        "",
    ]

    if report.recommendations:
        lines += ["RECOMMENDATIONS", "-" * 40]
        for rec in report.recommendations:
            lines.append(f"[{rec.urgency.upper():8s}] {rec.rule_id}  —  {rec.action_human}")
        lines.append("")

    # Flags — human-readable violation strings
    failed_details = [d for d in report.details if d.status == "VIOLATION"]
    if failed_details:
        lines += ["VIOLATIONS REQUIRING ACTION", "-" * 40]
        for d in failed_details:
            clause = f" ({d.source_clause})" if d.source_clause else ""
            reason = d.deviation_details.reason if d.deviation_details else "No details"
            lines.append(f"- [{d.rule_id}{clause}] {d.description}")
            lines.append(f"  Reason : {reason}")
            if d.action:
                lines.append(f"  Action : {d.action}")
            lines.append("")

    lines += ["RULE-BY-RULE DETAILS", "-" * 40]
    for d in report.details:
        status_str = {"PASS": "✅ PASS", "VIOLATION": "❌ VIOLATION", "SKIPPED": "⏭  SKIP"}.get(d.status, d.status)
        lines.append(f"{status_str}  {d.rule_id}  |  {d.description}")
        if d.deviation_details:
            lines.append(f"         Reason: {d.deviation_details.reason}")
        if d.action:
            lines.append(f"         Action: {d.action}")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Notification service
# ---------------------------------------------------------------------------

def _send_via_smtp(subject: str, body: str, to_email: str) -> None:
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    from state import DB_PATH

    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not gmail_address or not gmail_password:
        raise ValueError("Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in environment variables")

    msg = MIMEMultipart()
    msg['From'] = gmail_address
    msg['To'] = to_email
    msg['Subject'] = subject

    # Attach the email body
    msg.attach(MIMEText(body, 'plain'))

    # Attach the active ruleset JSON
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            part = MIMEApplication(f.read(), Name="active_ruleset.json")
        part['Content-Disposition'] = 'attachment; filename="active_ruleset.json"'
        msg.attach(part)

    # Connect to Gmail SMTP server
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.send_message(msg)
        logger.info(f"SMTP response: message sent successfully to {to_email}")


def send_notification(report: Report, recipient: str) -> str:
    """
    Deliver the compliance report.
    Generates a log of the email, and returns "sendgrid" or "console_log" to indicate path taken.
    """
    inv = report.invoice_number or "N/A"
    subject = f"AP Compliance Report — {report.summary.overall_status} — Invoice {inv}"
    body = _format_email_body(report, recipient)

    # 1. Generate Log File (Audit Log of emails)
    log_dir = os.path.join(os.path.dirname(__file__), "..", "email_logs")
    os.makedirs(log_dir, exist_ok=True)
    ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"email_inv_{inv}_{ts_str}.log")
    
    from state import DB_PATH
    rules_appendix = ""
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r", encoding="utf-8") as f:
            rules_appendix = f"\n\n--- ENCLOSED ATTACHMENT: active_ruleset.json ---\n{f.read()}\n"

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"TO: {recipient}\nSUBJECT: {subject}\n" + "="*72 + f"\n{body}{rules_appendix}")
        logger.info(f"Email log generated at: {log_path}")
    except Exception as e:
        logger.error(f"Failed to write email log: {e}")

    # 2. Attempt SMTP delivery
    gmail_pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if gmail_pwd:
        try:
            _send_via_smtp(subject, body, recipient)
            logger.info(f"Report sent via SMTP to {recipient}")
            return "smtp"
        except Exception as e:
            logger.error(f"SMTP send failed ({e}). Falling back to console log.")

    # 3. Console fallback
    print("\n" + "=" * 72)
    print(f"TO: {recipient}")
    print(f"SUBJECT: {subject}")
    print("=" * 72)
    print(body)
    print("=" * 72 + "\n")
    return "console_log"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/send-report", response_model=SendReportResponse)
async def send_report(body: SendReportRequest) -> SendReportResponse:
    """
    Build compliance report from execution results and send to recipient email.
    """
    if not body.execution_results:
        raise HTTPException(status_code=400, detail="execution_results must not be empty.")

    report = _build_report(body.execution_results, body.invoice_number)
    delivery_method = send_notification(report, body.email)

    return SendReportResponse(
        report=report,
        delivery_method=delivery_method,
        message=(
            f"Report delivered via {delivery_method} to {body.email}. "
            f"Status: {report.summary.overall_status}"
        ),
    )
