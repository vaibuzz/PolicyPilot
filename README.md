---
title: PolicyPilot
emoji: 🛡️
colorFrom: indigo
colorTo: purple
sdk: docker
pinned: false
---

# PolicyPilot — Financial Compliance & Audit Engine

**PolicyPilot** is an autonomous, human-in-the-loop financial compliance system. It converts unstructured corporate policies (PDF/MD/TXT) into a deterministic, programmatic rule engine using AI, enabling real-time, automated auditing of Invoices, Purchase Orders, and GRNs. 

This project was built to address the unreliability of purely LLM-based auditing by introducing a **hybrid architecture**: AI is used strictly for *extraction and translation*, while a purpose-built Python engine handles *mathematical evaluation and deterministic execution*.

---

## 🏗️ System Architecture

Our solution is entirely decoupled, consisting of a React Vite frontend and a FastAPI Python backend.

### 1. Multi-Modal Document Ingestion
Before AI can process a policy or an invoice, the documents must be converted into clean text.
- **Rich Document Extraction:** We use **IBM Docling** to parse dense financial tables, nested lists, and multi-column PDFs. This ensures complex matrixes (like Approval Authorities) retain their structural integrity.
- **Fail-Safe Fallback:** If a document is scanned or incompatible with Docling, the system seamlessly falls back to **PyMuPDF**, ensuring 100% ingestion reliability across `PDF`, `MD`, and `TXT` files.

### 2. The AI Translation Pipeline (Groq + Llama 3)
Instead of asking an LLM to blindly read a policy and audit an invoice in one prompt, we use **Groq (Llama 3.3 70B)** to translate the unstructured corporate policies into strict, machine-readable rules. 
- **Two-Pass Extraction:** The LLM first extracts rules into a strict Pydantic JSON schema (`Rule` objects). It then makes a second pass to identify edge cases, missing parameters, or logical overlap between rules.
- **Confidence Scoring:** The LLM attaches a `confidence_score` to every extracted rule. Any rule below 90% confidence or with detected conflicts is flagged in the UI for Human-In-The-Loop (HITL) resolution.

### 3. Why Python for Evaluation? (The Deterministic Rule Engine)
A common mistake in AI engineering is using LLMs to calculate math or execute logic against active data. LLMs hallucinate complex calculations and are fundamentally non-deterministic. To solve this, once rules are finalized by a human, they are compiled mathematically.
- We developed a completely custom `Rule Evaluator` in Python. It parses the JSON logic (e.g., `amount > 50000 AND (PO_number = missing OR GSTIN = mismatched)`) into a safe, secure Abstract Syntax Tree.
- **The Advantages over LLM Evaluation:** During live invoice processing, **zero LLM calls are made**. The evaluator checks the invoice payload against the rules identically every single time. This eliminates prompt-injection risks, removes token costs entirely, processes documents in milliseconds instead of seconds, and ensures 100% deterministic, audit-ready compliance.

### 4. Human-In-The-Loop UI & Audit Trail
- The React UI calculates real-time confidence metrics. If a human modifies or accepts a flagged rule, its confidence permanently elevates to 100%, updating the organization's system trust score dynamically.
- The system persists the active ruleset to disk and generates hard-copy `.log` files of every email dispatched by the native Python SMTP client, ensuring enterprise traceability.

---

## 🛠️ How AI Tools Were Leveraged
*As per submission guidelines, here is a note on how AI tooling accelerated development:*

This project was built leveraging **Google Antigravity (Advanced Agentic Architecture)**. We effectively utilized AI agent pair-programming to tackle complex integration points:
1. **Mathematical Parsing:** Antigravity was used to rapidly prototype the complex recursive Regex logic required to transition from an English-language policy (e.g. "+/- 10% tolerance") into strict Python Boolean/Math operations without using `eval()`.
2. **Schema Enforcement:** We used the AI to write robust Pydantic schemas that forced the Groq Llama3 model to output perfect JSON.
3. **Iterative Debugging:** When hitting Groq 429 Rate Limit issues, the AI agent autonomously diagnosed the terminal error, dynamically swapped API keys in the `.env` file, and kept the server hot-reloading smoothly.

---

## 💻 Sample Input / Output

### System 1: Policy Extraction
**Input (Unstructured Policy Document):**
> "Invoices between INR 10,00,001 and INR 50,00,000 must be escalated to the Finance Controller for review."

**Output (Structured JSON Rule):**
```json
{
  "rule_id": "AP-APR-003",
  "description": "Escalate high value invoices to Finance Controller",
  "conditions": [
    "Invoice_table.amount BETWEEN 1000001 AND 5000000"
  ],
  "action": "ESCALATE_TO_FINANCE_CONTROLLER",
  "confidence_score": 0.96
}
```

### System 2: Deterministic Execution
**Input (Extracted Invoice Payload):**
```json
{ "invoice_number": "INV-2044", "amount": 2500000, "vendor_status": "APPROVED" }
```

**Output (Execution Report):**
```json
{
  "status": "VIOLATION",
  "rule_id": "AP-APR-003",
  "action": "ESCALATE_TO_FINANCE_CONTROLLER",
  "deviation_details": {
    "reason": "Invoice amount (2500000) fell within high tier (1m-5m)"
  }
}
```

---

## 🚀 Running Locally

### Backend (Python)
```bash
cd backend
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
# Set GROQ_API_KEY and GMAIL_APP_PASSWORD in .env
python main.py
```

### Frontend (React/Vite)
```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```
