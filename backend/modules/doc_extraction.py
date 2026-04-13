"""
Module 5 — Document Upload and Structured Extraction
POST /extract-documents

Accepts up to 3 file uploads (Invoice PDF, PO PDF, GRN PDF).
1. Runs Docling/PyMuPDF on all PDFs in parallel (asyncio)
2. Extracts field vocabulary from active ruleset
3. Single LLM call (Anthropic Claude primary, Groq fallback) with all document content + field vocab
4. Python normalization pass (strip Indian comma formatting)
Returns: merged DocumentPayload JSON
"""

import asyncio
import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import state
from models.schemas import DocumentPayload, ExtractDocumentsResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Parser (reuse the same Docling/PyMuPDF detection from ingestion)
# ---------------------------------------------------------------------------

_DOCLING_AVAILABLE = False
_PYMUPDF_AVAILABLE = False

try:
    from docling.document_converter import DocumentConverter  # type: ignore
    _DOCLING_AVAILABLE = True
except Exception:
    pass

if not _DOCLING_AVAILABLE:
    try:
        import fitz  # type: ignore
        _PYMUPDF_AVAILABLE = True
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Document system prompt — fully dynamic, zero hardcoded field names
# ---------------------------------------------------------------------------

DOC_EXTRACTION_SYSTEM_PROMPT = """You are a document data extraction specialist. You will be given the raw text of one or more AP documents — an Invoice, a Purchase Order (PO), and/or a Goods Receipt Note (GRN).

You will also receive a FIELD VOCABULARY grouped by table. These field names come directly from the company's AP policy. Your ONLY job is to find the value for each field in the documents and return it in the exact JSON structure shown below.

JSON output structure:
{
  "Invoice_table": {
    "line_items": [
      {
        "item": "string (You MUST use this exact key for the line item description/name)",
        "<<Insert extra fields from INVOICE VOCABULARY here>>": "..."
      }
    ],
    "<<Insert extra fields from INVOICE VOCABULARY here>>": "..."
  },
  "PO_table": {
    "line_items": [
      {
        "item": "string (MUST use exactly 'item')",
        "<<Insert extra fields from PO VOCABULARY here>>": "..."
      }
    ],
    "<<Insert extra fields from PO VOCABULARY here>>": "..."
  },
  "GRN_table": {
    "line_items": [
      {
        "item": "string (MUST use exactly 'item')",
        "<<Insert extra fields from GRN VOCABULARY here>>": "..."
      }
    ],
    "<<Insert extra fields from GRN VOCABULARY here>>": "..."
  },
  "Vendor_table": {
    "<<Insert fields from VENDOR VOCABULARY here>>": "..."
  }
}

Field extraction rules:
1. Use ONLY the field names given in the FIELD VOCABULARY (plus 'item' inside line_items). Do not invent names for root fields.
2. If a field value is not present in the document, set it to null.
3. ALL date fields must be returned as strings in YYYY-MM-DD format.
4. ALL numeric values must be returned as plain floats — strip Indian comma formatting (e.g. 1,00,000 → 100000.0).
5. Boolean fields (e.g. handwritten, watchlist): return true or false (JSON booleans, not strings).
6. For "supply_type": read the document carefully. If the supply is within the same state, use the exact value shown in the vocabulary (e.g. "intra" or "intra-state" — whichever is listed). If between states, use the inter variant.
7. For line items, structure them exactly in the array format above. The line item description MUST use the key "item". Use the identical item name string across Invoice, PO, and GRN tables.
8. If a whole document type was NOT uploaded (e.g. no PO provided), set that entire table to null — never return an empty object {}.
9. For computed fields like "gstin_pan" (characters 3-12 of GSTIN): derive the value yourself from the raw GSTIN string present in the document.
10. CRITICAL — Always extract ALL date fields from EVERY document, even if not explicitly listed in the vocabulary. PO documents typically have "PO Issue Date" and "PO Validity Date" — these MUST be returned under PO_table as "date" (the issue date) in YYYY-MM-DD format. Similarly, always extract "taxable_amount" or "Sub-Total (Taxable Amount)" from invoices when visible.
11. Return ONLY raw valid JSON. No markdown code fences (```), no explanation, no extra text.

Return a raw JSON object only matching the exact structure specified. Do not add any fields not shown in the structure. Do not wrap in markdown. The first character must be the opening brace."""


# ---------------------------------------------------------------------------
# Field vocabulary extraction from active ruleset
# ---------------------------------------------------------------------------

# Known root table keys the engine understands
_TABLE_KEYS = ("Invoice_table", "PO_table", "GRN_table", "Vendor_table")


def _extract_field_vocabulary(rules: List[Any]) -> List[str]:
    """
    Walk every condition tree (including suggested_fix conditions) and collect
    all Table_name.field_name references. Strips expression suffixes so
    "PO_table.amount * 1.01" contributes "PO_table.amount" to the vocab.
    """
    vocab: set = set()

    def _walk(cond: Any) -> None:
        if not isinstance(cond, dict):
            return
        for key in ("left", "right", "lower", "upper"):
            val = cond.get(key)
            if isinstance(val, str) and "." in val:
                # Strip arithmetic expression suffix: "PO_table.amount * 1.01" → "PO_table.amount"
                clean = re.split(r"\s*[\+\-\*/]\s*", val)[0].strip()
                if any(clean.startswith(t + ".") for t in _TABLE_KEYS):
                    vocab.add(clean)
        for operand in cond.get("operands") or []:
            _walk(operand)

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        _walk(rule.get("condition") or {})
        # Also walk suggested_fix conditions so fixed rules contribute fields
        fix = rule.get("suggested_fix")
        if isinstance(fix, dict):
            _walk(fix.get("condition") or {})

    return sorted(vocab)


def _group_vocab_by_table(vocab: List[str]) -> Dict[str, List[str]]:
    """
    Group a flat vocabulary list by table name.
    e.g. ["Invoice_table.amount", "PO_table.qty"] →
         {"Invoice_table": ["amount"], "PO_table": ["qty"]}
    Strips the [*] array notation for display clarity.
    """
    grouped: Dict[str, List[str]] = {t: [] for t in _TABLE_KEYS}
    for path in vocab:
        for table in _TABLE_KEYS:
            if path.startswith(table + "."):
                field = path[len(table) + 1:]
                # Strip [*] suffix: "line_items[*].qty" → "line_items[*].qty" kept as-is
                # but simple paths like "amount" are added directly
                if field not in grouped[table]:
                    grouped[table].append(field)
                break
    return grouped


# ---------------------------------------------------------------------------
# PDF → text helper (mirrors ingestion.py logic)
# ---------------------------------------------------------------------------

async def _pdf_to_text(file_bytes: bytes, filename: str) -> str:
    """Non-blocking PDF text extraction (runs sync parser in thread pool)."""
    loop = asyncio.get_event_loop()

    def _sync_parse():
        if _DOCLING_AVAILABLE:
            try:
                from docling.document_converter import DocumentConverter  # type: ignore
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    converter = DocumentConverter()
                    result = converter.convert(tmp_path)
                    return result.document.export_to_markdown()
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(f"Docling failed for {filename}: {e}, trying PyMuPDF")

        if _PYMUPDF_AVAILABLE:
            import fitz  # type: ignore
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            pages = [page.get_text("text") for page in doc]
            doc.close()
            return "\n\n".join(pages)

        raise RuntimeError(
            "No PDF parser available. Install docling or PyMuPDF."
        )

    return await loop.run_in_executor(None, _sync_parse)


# ---------------------------------------------------------------------------
# Normalization pass — strip Indian comma formatting from numbers
# ---------------------------------------------------------------------------

def _normalize_numbers(obj: Any) -> Any:
    """
    Recursively walk the parsed JSON and:
    - Convert strings like "1,00,000" to floats (Indian comma formatting)
    - Convert "true"/"false" strings to booleans
    - Convert "NIL", "NA", "-", "" to None
    - Leave other strings intact
    """
    if isinstance(obj, dict):
        return {k: _normalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_numbers(v) for v in obj]
    if isinstance(obj, str):
        val = obj.strip().lower()
        # Empty or Nil values -> None (fixes Pydantic float parsing errors)
        if val in ("nil", "n/a", "na", "-", "", "null", "none"):
            return None
        # Boolean strings
        if val == "true":
            return True
        if val == "false":
            return False
        # Indian/international comma-formatted numbers
        stripped = obj.replace(",", "")
        try:
            return float(stripped)
        except ValueError:
            return obj
    return obj


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/extract-documents", response_model=ExtractDocumentsResponse)
async def extract_documents(
    invoice: Optional[UploadFile] = File(None),
    po: Optional[UploadFile] = File(None),
    grn: Optional[UploadFile] = File(None),
) -> ExtractDocumentsResponse:
    """
    Accept up to 3 document uploads, extract structured data via Claude,
    and return a merged DocumentPayload aligned with the active ruleset's
    field vocabulary.
    """
    if not any([invoice, po, grn]):
        raise HTTPException(status_code=400, detail="At least one document must be uploaded.")

    # ---- Read all file bytes ------------------------------------------------
    file_map: Dict[str, Optional[bytes]] = {
        "Invoice": await invoice.read() if invoice else None,
        "Purchase Order": await po.read() if po else None,
        "Goods Receipt Note": await grn.read() if grn else None,
    }
    documents_received = [label for label, b in file_map.items() if b is not None]

    # ---- Convert PDFs to text in parallel -----------------------------------
    tasks = {}
    for label, file_bytes in file_map.items():
        if file_bytes is not None:
            fname = f"{label}.pdf"
            tasks[label] = asyncio.create_task(_pdf_to_text(file_bytes, fname))

    doc_texts: Dict[str, str] = {}
    for label, task in tasks.items():
        try:
            doc_texts[label] = await task
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse {label}: {e}")

    # ---- Build user message with structured per-table vocabulary -----------
    field_vocab = _extract_field_vocabulary(state.active_ruleset)
    grouped = _group_vocab_by_table(field_vocab)

    # Build a clear, per-table vocabulary block so the LLM knows exactly
    # which field belongs under which root table key
    vocab_lines = ["FIELD VOCABULARY (extract these exact fields from the documents):"]
    for table, fields in grouped.items():
        if fields:
            vocab_lines.append(f"\n{table}:")
            for f in fields:
                # Provide a type hint for known field patterns
                if "date" in f:
                    hint = "string YYYY-MM-DD or null"
                elif f in ("watchlist", "handwritten"):
                    hint = "boolean (true/false) or null"
                elif "line_items" in f or f in ("item",):
                    hint = "array (see rule 7 in system prompt)"
                else:
                    hint = "number or null if numeric, string or null if text"
                vocab_lines.append(f"  - {f}  [{hint}]")

    vocab_block = "\n".join(vocab_lines)

    doc_sections = "\n\n".join(
        f"=== {label.upper()} DOCUMENT ===\n{text}"
        for label, text in doc_texts.items()
    )

    user_message = f"{vocab_block}\n\n{doc_sections}"

    # ---- Single LLM call (Anthropic primary, Groq fallback) -----------------
    from modules.extraction import get_llm_client, call_llm_with_fallback

    logger.info(f"Calling LLM for document extraction. Docs: {documents_received}")

    primary_type, primary_client, fallback_client = get_llm_client()

    def _call_llm() -> str:
        response_text, _ = call_llm_with_fallback(
            primary_client, primary_type, fallback_client,
            system_prompt=DOC_EXTRACTION_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=8000,
            response_format_json=False,
        )
        return response_text

    try:
        raw = await asyncio.to_thread(_call_llm)
        raw = raw.strip()
    except Exception as e:
        logger.error(f"LLM API Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"LLM API Error: {str(e)}")
    # Strip accidental code fences
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Claude extraction returned invalid JSON: {e}\n{raw[:400]}")
        raise HTTPException(status_code=502, detail=f"Claude returned invalid JSON: {e}")

    # ---- Normalization pass --------------------------------------------------
    normalized = _normalize_numbers(extracted)

    # ---- Inject computed fields into Invoice_table ---------------------------
    # Dynamic: look up taxable/tax/total field names from vocabulary so this
    # works regardless of what the policy calls those fields.
    inv = normalized.get("Invoice_table")
    if isinstance(inv, dict):
        # Identify the field names for taxable amount, tax amount, and total
        # from the vocabulary (fall back to common names if not in vocab)
        inv_fields = grouped.get("Invoice_table", [])
        taxable_field = next((f for f in inv_fields if "taxable" in f), "taxable_amount")
        tax_field     = next((f for f in inv_fields if f in ("tax_amount", "igst", "cgst", "sgst") and "taxable" not in f), "tax_amount")
        total_field   = next((f for f in inv_fields if "grand_total" in f or f in ("total", "amount", "grand_total")), "grand_total")

        taxable = inv.get(taxable_field)
        tax     = inv.get(tax_field)
        total   = inv.get(total_field)
        if taxable is not None and tax is not None and total is not None:
            try:
                inv["tax_calculation_error"] = abs(
                    float(taxable) + float(tax) - float(total)
                )
            except (TypeError, ValueError):
                inv["tax_calculation_error"] = None

        # Derive gstin_pan if gstin is present (characters 3-12 of GSTIN = PAN)
        if "gstin_pan" in inv_fields and inv.get("gstin_pan") is None:
            gstin_val = inv.get("gstin") or ""
            if len(gstin_val) >= 12:
                inv["gstin_pan"] = gstin_val[2:12]

        # --- Derive tax_amount if null ----------------------------------------
        # The LLM extracts cgst/sgst/igst individually but leaves tax_amount null.
        # For intra-state: tax_amount = cgst + sgst
        # For inter-state: tax_amount = igst
        # This is needed so rules like AP-TAX-003 (which check
        # taxable_amount + tax_amount == grand_total) can evaluate.
        if inv.get("tax_amount") is None:
            supply_type_raw = (inv.get("supply_type") or "").strip().lower()
            if supply_type_raw in ("intra", "intra-state"):
                cgst = inv.get("cgst")
                sgst = inv.get("sgst")
                if cgst is not None and sgst is not None:
                    try:
                        inv["tax_amount"] = float(cgst) + float(sgst)
                        logger.debug(f"Derived tax_amount from cgst+sgst: {inv['tax_amount']}")
                    except (TypeError, ValueError):
                        pass
            elif supply_type_raw in ("inter", "inter-state"):
                igst = inv.get("igst")
                if igst is not None:
                    try:
                        inv["tax_amount"] = float(igst)
                        logger.debug(f"Derived tax_amount from igst: {inv['tax_amount']}")
                    except (TypeError, ValueError):
                        pass

        # --- Derive taxable_amount if null ------------------------------------
        # If the LLM missed the Sub-Total / Taxable Amount, we can compute it
        # from grand_total - tax_amount.  This is needed for AP-TAX-003.
        if inv.get("taxable_amount") is None:
            gt = inv.get("grand_total")
            ta = inv.get("tax_amount")
            if gt is not None and ta is not None:
                try:
                    inv["taxable_amount"] = float(gt) - float(ta)
                    logger.debug(f"Derived taxable_amount = grand_total - tax_amount: {inv['taxable_amount']}")
                except (TypeError, ValueError):
                    pass

        # --- FIX 1a: Mirror grand_total → amount if amount is null -----------
        # Many rules reference Invoice_table.amount, but the LLM often puts the
        # total into grand_total. Mirror the value so both fields are available.
        if inv.get("amount") is None and inv.get("grand_total") is not None:
            inv["amount"] = inv["grand_total"]
            logger.debug("Mirrored Invoice_table.grand_total → amount")
        elif inv.get("grand_total") is None and inv.get("amount") is not None:
            inv["grand_total"] = inv["amount"]
            logger.debug("Mirrored Invoice_table.amount → grand_total")

        # --- FIX 1d: Normalize supply_type -----------------------------------
        # LLM may return "intra" or "inter" but rules may use the full form.
        # Normalize to the full form to prevent false equality mismatches.
        st = inv.get("supply_type")
        if isinstance(st, str):
            st_lower = st.strip().lower()
            if st_lower == "intra":
                inv["supply_type"] = "intra-state"
                logger.debug("Normalized supply_type: 'intra' → 'intra-state'")
            elif st_lower == "inter":
                inv["supply_type"] = "inter-state"
                logger.debug("Normalized supply_type: 'inter' → 'inter-state'")

        # --- FIX 4b: Normalize place_of_supply -------------------------------
        # If place_of_supply contains "State (Code)", extract just the code so
        # equality comparisons with the buyer state code work perfectly.
        pos = inv.get("place_of_supply")
        if isinstance(pos, str):
            m = re.search(r"\((\d+)\)", pos)
            if m:
                inv["place_of_supply"] = m.group(1)
                logger.debug(f"Normalized place_of_supply: '{pos}' → '{m.group(1)}'")

        # Initialise deviation flags — rule engine will set these to True on FAIL
        inv.setdefault("has_deviation", False)
        inv.setdefault("has_compliance_failure", False)

    # ---- FIX 1b: Compute PO_table.amount from line items if missing ----------
    po = normalized.get("PO_table")
    if isinstance(po, dict):
        if po.get("amount") is None:
            po_line_items = po.get("line_items") or []
            if po_line_items:
                try:
                    po_total = sum(
                        float(li.get("amount", 0) or 0) for li in po_line_items
                    )
                    po["amount"] = po_total
                    logger.debug(f"Computed PO_table.amount from line items: {po_total}")
                except (TypeError, ValueError):
                    logger.warning("Could not compute PO_table.amount from line items")

        # --- Derive PO_table.date from grand_total-based line item dates ------
        # If PO date is still null, try extracting from known PO amount date
        # (the LLM sometimes misses the PO Issue Date header)
        if po.get("date") is None and po.get("grand_total") is None:
            # Mirror amount → grand_total for PO too
            if po.get("amount") is not None:
                po["grand_total"] = po["amount"]
                logger.debug("Mirrored PO_table.amount → grand_total")

    # ---- Compute invoice_po_age_days for AP-TWM-004 --------------------------
    # This derived field lets rules check "invoice submitted > 90 days after PO"
    # without requiring the LLM to produce a computed field.
    inv_obj = normalized.get("Invoice_table")
    po_obj  = normalized.get("PO_table")
    if isinstance(inv_obj, dict) and isinstance(po_obj, dict):
        inv_date_str = inv_obj.get("date")
        po_date_str  = po_obj.get("date")
        if inv_date_str and po_date_str:
            import datetime
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    inv_date = datetime.datetime.strptime(str(inv_date_str).strip(), fmt).date()
                    po_date  = datetime.datetime.strptime(str(po_date_str).strip(), fmt).date()
                    age_days = (inv_date - po_date).days
                    inv_obj["invoice_po_age_days"] = age_days
                    logger.debug(f"Computed invoice_po_age_days = {age_days}")
                    break
                except ValueError:
                    continue

    # ---- FIX 1c & 4a: Inject derived fields into Vendor_table ----------------
    vendor = normalized.get("Vendor_table")
    if isinstance(vendor, dict):
        gstin_val = vendor.get("gstin") or ""
        if len(gstin_val) >= 2 and vendor.get("buyer_gstin_state_code") is None:
            vendor["buyer_gstin_state_code"] = gstin_val[:2]
            logger.debug(f"Injected Vendor_table.buyer_gstin_state_code = {gstin_val[:2]}")
        # Inject Vendor PAN (chars 3-12 of GSTIN)
        if len(gstin_val) >= 12 and vendor.get("pan") is None:
            vendor["pan"] = gstin_val[2:12]
            logger.debug(f"Injected Vendor_table.pan = {gstin_val[2:12]}")

    # ---- Unmatched line item detection (pre-execution warning) ---------------
    pre_execution_warnings: List[str] = []
    inv_items = (normalized.get("Invoice_table") or {}).get("line_items") or []
    po_items  = (normalized.get("PO_table") or {}).get("line_items") or []
    if inv_items and po_items:
        po_names = {(i.get("item") or "").strip().lower() for i in po_items}
        for itm in inv_items:
            name = (itm.get("item") or "").strip().lower()
            if name and name not in po_names:
                pre_execution_warnings.append(
                    f"Invoice line item '{itm.get('item')}' has no matching PO line item."
                )
        if pre_execution_warnings:
            logger.warning(
                f"Pre-execution warnings: {pre_execution_warnings}"
            )

    # ---- Validate via Pydantic ----------------------------------------------
    try:
        payload = DocumentPayload.model_validate(normalized)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Extraction schema mismatch: {e}")

    return ExtractDocumentsResponse(
        payload=payload,
        field_vocabulary=field_vocab,
        documents_received=documents_received,
    )
