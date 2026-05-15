/**
 * SidePanel — right-side drawer for document upload and rule testing.
 * Slides in over the ReviewScreen without navigation.
 */
import React, { useState, useRef } from 'react'
import { extractDocuments, executeRules, sendReport } from '../api/client'

const STATUS_CONFIG = {
  PASS:      { label: 'Pass',      cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
  VIOLATION: { label: 'Violation', cls: 'bg-red-500/20 text-red-300 border-red-500/30' },
  SKIPPED:   { label: 'Skipped',   cls: 'bg-slate-500/20 text-slate-400 border-slate-500/30' },
}

/** Derive the action type from the results array */
function deriveActionType(results) {
  if (!results) return null
  const violations = results.filter(r => r.status === 'VIOLATION')
  if (violations.length === 0) return 'CRM'

  const actions = violations.map(r => (r.action || '').toUpperCase())

  if (actions.some(a => /HOLD/.test(a)))       return 'HOLD'
  if (actions.some(a => /REJECT/.test(a)))      return 'REJECT'
  if (actions.some(a => /FLAG/.test(a)))        return 'FLAG'
  if (actions.some(a => /COMPLIANCE/.test(a)))  return 'COMPLIANCE'
  if (actions.some(a =>
    /ESCALATE|ROUTE|APPROVE|DEPARTMENT|PERSON|CLERK|HEAD|CFO|CONTROLLER|PROCUREMENT/.test(a)
  ))                                             return 'APPROVAL'

  return 'FLAG'
}

/** Config for each action type — icon, title, body, colours */
const ACTION_CONFIG = {
  CRM: {
    icon: '✅',
    iconBg: 'bg-emerald-500/20 border-emerald-500/30',
    iconColor: 'text-emerald-400',
    title: 'Added to CRM',
    body: 'All rules passed. This invoice and PO have been logged as COMPLIANT and added to the CRM system.',
    pill: 'COMPLIANT',
    pillCls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    btnCls: 'bg-emerald-600 hover:bg-emerald-500',
    btnLabel: 'Done',
  },
  APPROVAL: {
    icon: '📤',
    iconBg: 'bg-blue-500/20 border-blue-500/30',
    iconColor: 'text-blue-400',
    title: 'Sent for Approval',
    body: 'One or more rules require human review. The document has been routed to the relevant approver / department for sign-off.',
    pill: 'PENDING APPROVAL',
    pillCls: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
    btnCls: 'bg-blue-600 hover:bg-blue-500',
    btnLabel: 'Understood',
  },
  HOLD: {
    icon: '🗂️',
    iconBg: 'bg-amber-500/20 border-amber-500/30',
    iconColor: 'text-amber-400',
    title: 'Added to Hold Folder',
    body: 'The invoice has been placed in the Hold queue pending resolution of flagged conditions. No payment will be processed until holds are cleared.',
    pill: 'ON HOLD',
    pillCls: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
    btnCls: 'bg-amber-600 hover:bg-amber-500',
    btnLabel: 'Got it',
  },
  REJECT: {
    icon: '🚫',
    iconBg: 'bg-red-500/20 border-red-500/30',
    iconColor: 'text-red-400',
    title: 'Invoice Rejected',
    body: 'This invoice has been rejected and returned to the vendor. A rejection notice has been logged in the audit trail.',
    pill: 'REJECTED',
    pillCls: 'bg-red-500/20 text-red-300 border-red-500/30',
    btnCls: 'bg-red-600 hover:bg-red-500',
    btnLabel: 'Close',
  },
  FLAG: {
    icon: '🚩',
    iconBg: 'bg-orange-500/20 border-orange-500/30',
    iconColor: 'text-orange-400',
    title: 'Flagged for Review',
    body: 'The document has been flagged and assigned to the AP team for manual review. It will remain in the review queue until resolved.',
    pill: 'FLAGGED',
    pillCls: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
    btnCls: 'bg-orange-600 hover:bg-orange-500',
    btnLabel: 'Acknowledged',
  },
  COMPLIANCE: {
    icon: '⚖️',
    iconBg: 'bg-purple-500/20 border-purple-500/30',
    iconColor: 'text-purple-400',
    title: 'Compliance Hold Applied',
    body: 'A compliance violation was detected. The document has been placed under a compliance hold and routed to the internal audit team.',
    pill: 'COMPLIANCE HOLD',
    pillCls: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    btnCls: 'bg-purple-600 hover:bg-purple-500',
    btnLabel: 'Acknowledged',
  },
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function UploadZone({ label, sublabel, file, onChange, accept = '.pdf' }) {
  const inputRef = useRef()

  function handleDrop(e) {
    e.preventDefault()
    const dropped = e.dataTransfer.files[0]
    if (dropped) onChange(dropped)
  }

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
      className={`relative border-2 border-dashed rounded-xl p-4 cursor-pointer transition-all duration-200 text-center
        ${file
          ? 'border-emerald-500/50 bg-emerald-950/20'
          : 'border-slate-600 hover:border-slate-500 bg-slate-800/30'
        }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => onChange(e.target.files[0])}
      />
      <p className="text-sm font-medium text-slate-300">{label}</p>
      <p className="text-xs text-slate-500 mt-0.5">{sublabel}</p>
      {file ? (
        <p className="text-xs text-emerald-400 mt-2 truncate">✓ {file.name}</p>
      ) : (
        <p className="text-xs text-slate-600 mt-2">Click or drag PDF here</p>
      )}
    </div>
  )
}

function ResultRow({ result }) {
  const cfg = STATUS_CONFIG[result.status] || STATUS_CONFIG.SKIPPED
  const [expanded, setExpanded] = useState(result.status === 'VIOLATION')

  return (
    <div className="border border-slate-700/50 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-start gap-3 p-3 text-left hover:bg-slate-800/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-medium border ${cfg.cls}`}>
          {cfg.label}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-mono text-cyan-400">{result.rule_id}</p>
          <p className="text-xs text-slate-300 mt-0.5 line-clamp-2">{result.description}</p>
        </div>
        <span className="text-slate-600 text-xs shrink-0">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && result.deviation_details && (
        <div className="px-3 pb-3 text-xs text-slate-400 border-t border-slate-700/50 mt-1 pt-2">
          <p className="text-slate-300">{result.deviation_details.reason}</p>
          {result.action && (
            <p className="mt-1 text-amber-400">
              → Action: {result.action.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** Full-screen modal popup confirming what action was taken */
function ActionPopup({ actionType, invoiceNumber, onDone }) {
  const cfg = ACTION_CONFIG[actionType] || ACTION_CONFIG.FLAG

  return (
    /* Backdrop */
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      {/* Card */}
      <div className="relative w-full max-w-sm bg-slate-900 border border-slate-700/60 rounded-2xl shadow-2xl overflow-hidden animate-slide-up">

        {/* Coloured top stripe */}
        <div className={`h-1 w-full ${cfg.btnCls.split(' ')[0]}`} />

        <div className="p-6 space-y-5">
          {/* Icon + pill */}
          <div className="flex items-center gap-3">
            <span className={`text-3xl w-14 h-14 flex items-center justify-center rounded-xl border ${cfg.iconBg}`}>
              {cfg.icon}
            </span>
            <div>
              <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold border ${cfg.pillCls}`}>
                {cfg.pill}
              </span>
              <h3 className="text-white font-bold text-lg mt-1 leading-tight">{cfg.title}</h3>
            </div>
          </div>

          {/* Invoice number badge */}
          {invoiceNumber && (
            <div className="flex items-center gap-2 bg-slate-800/60 border border-slate-700/40 rounded-lg px-3 py-2">
              <span className="text-slate-500 text-xs">Invoice</span>
              <span className="font-mono text-cyan-400 text-sm font-medium">{invoiceNumber}</span>
            </div>
          )}

          {/* Body text */}
          <p className="text-slate-300 text-sm leading-relaxed">{cfg.body}</p>

          {/* Timestamp */}
          <p className="text-slate-600 text-xs">
            Action recorded at {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </p>

          {/* CTA */}
          <button
            onClick={onDone}
            className={`w-full py-2.5 rounded-xl text-white font-semibold text-sm transition-all ${cfg.btnCls}`}
          >
            {cfg.btnLabel} — Upload Next Document
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function SidePanel({ open, onClose }) {
  const [files, setFiles]         = useState({ invoice: null, po: null, grn: null })
  const [loading, setLoading]     = useState(false)
  const [loadingMsg, setLoadingMsg] = useState('')
  const [results, setResults]     = useState(null)
  const [payload, setPayload]     = useState(null)
  const [error, setError]         = useState('')
  const [sending, setSending]     = useState(false)
  // popup state
  const [showPopup, setShowPopup] = useState(false)
  const [popupAction, setPopupAction] = useState(null)
  const [popupInvoice, setPopupInvoice] = useState(null)

  function setFile(key) {
    return (file) => setFiles((f) => ({ ...f, [key]: file }))
  }

  /** Reset everything back to the empty upload screen */
  function resetPanel() {
    setFiles({ invoice: null, po: null, grn: null })
    setResults(null)
    setPayload(null)
    setError('')
    setSending(false)
    setShowPopup(false)
    setPopupAction(null)
    setPopupInvoice(null)
  }

  async function handleRunAnalysis() {
    if (!files.invoice && !files.po && !files.grn) {
      setError('Upload at least one document.')
      return
    }
    setError('')
    setResults(null)
    setLoading(true)

    try {
      setLoadingMsg('Extracting document data…')
      const extractRes = await extractDocuments(files)
      const extractedPayload = extractRes.data.payload
      setPayload(extractedPayload)

      setLoadingMsg('Running rule engine…')
      const execRes = await executeRules(extractedPayload)
      setResults(execRes.data.results)
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Analysis failed.')
    } finally {
      setLoading(false)
      setLoadingMsg('')
    }
  }

  async function handleAction() {
    if (!results || sending) return
    setSending(true)
    setError('')

    const actionType   = deriveActionType(results)
    const invoiceNum   = payload?.Invoice_table?.invoice_number ?? null

    try {
      await sendReport(results, 'system@crm.local', invoiceNum)
      // Show contextual confirmation popup
      setPopupAction(actionType)
      setPopupInvoice(invoiceNum)
      setShowPopup(true)
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Action failed.')
    } finally {
      setSending(false)
    }
  }

  // Determine action button label from results
  let buttonText = 'Process Document'
  if (results) {
    const t = deriveActionType(results)
    const labels = {
      CRM: '✅ Add to CRM',
      APPROVAL: '📤 Send for Approval',
      HOLD: '🗂️ Add to Hold Files',
      REJECT: '🚫 Reject Invoice',
      FLAG: '🚩 Flag for Review',
      COMPLIANCE: '⚖️ Apply Compliance Hold',
    }
    buttonText = labels[t] || 'Process Document'
  }

  const passed  = results?.filter((r) => r.status === 'PASS').length      ?? 0
  const failed  = results?.filter((r) => r.status === 'VIOLATION').length  ?? 0
  const skipped = results?.filter((r) => r.status === 'SKIPPED').length   ?? 0

  return (
    <>
      {/* Action confirmation popup */}
      {showPopup && (
        <ActionPopup
          actionType={popupAction}
          invoiceNumber={popupInvoice}
          onDone={resetPanel}      /* closes popup AND resets the upload screen */
        />
      )}

      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-20"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-full sm:w-[60vw] bg-slate-900 border-l border-slate-700/50 z-30 overflow-y-auto shadow-2xl transition-transform duration-300 ${
          open ? 'translate-x-0 animate-slide-in' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/50 sticky top-0 bg-slate-900 z-10">
          <div>
            <h2 className="text-base font-semibold text-white">Test These Rules</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Upload documents to validate against your finalized ruleset
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white text-xl font-light transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Upload zones */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-slate-300">Documents</h3>
            <UploadZone
              label="Invoice PDF"
              sublabel="Required for amount and tax checks"
              file={files.invoice}
              onChange={setFile('invoice')}
            />
            <UploadZone
              label="Purchase Order PDF"
              sublabel="Required for three-way match"
              file={files.po}
              onChange={setFile('po')}
            />
          </div>

          {/* Run Analysis */}
          <button
            onClick={handleRunAnalysis}
            disabled={loading}
            className="w-full py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-white font-semibold text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="inline-block w-4 h-4 border-2 border-purple-300 border-t-transparent rounded-full animate-spin" />
                {loadingMsg || 'Analysing…'}
              </span>
            ) : (
              '⚡ Run Analysis'
            )}
          </button>

          {error && (
            <p className="text-red-400 text-sm bg-red-950/30 border border-red-500/20 rounded-lg p-3">
              {error}
            </p>
          )}

          {/* Results */}
          {results && (
            <div className="space-y-3 animate-fade-in">
              {/* Summary row */}
              <div className="flex gap-3 text-center">
                <div className="flex-1 bg-emerald-950/30 border border-emerald-500/20 rounded-lg py-2">
                  <div className="text-lg font-bold text-emerald-400">{passed}</div>
                  <div className="text-xs text-slate-500">Pass</div>
                </div>
                <div className="flex-1 bg-red-950/30 border border-red-500/20 rounded-lg py-2">
                  <div className="text-lg font-bold text-red-400">{failed}</div>
                  <div className="text-xs text-slate-500">Violations</div>
                </div>
                <div className="flex-1 bg-slate-800/50 border border-slate-600/30 rounded-lg py-2">
                  <div className="text-lg font-bold text-slate-400">{skipped}</div>
                  <div className="text-xs text-slate-500">Skipped</div>
                </div>
              </div>

              {/* Overall status */}
              <div
                className={`text-center py-2 rounded-lg text-sm font-semibold ${
                  failed === 0
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    : 'bg-red-500/10 text-red-400 border border-red-500/20'
                }`}
              >
                {failed === 0
                  ? '✓ COMPLIANT'
                  : `✗ NON-COMPLIANT — ${failed} violation${failed > 1 ? 's' : ''}`}
              </div>

              {/* Rule rows */}
              <div className="space-y-2">
                {results.map((r) => (
                  <ResultRow key={r.rule_id} result={r} />
                ))}
              </div>

              {/* Dynamic Action Button */}
              <button
                onClick={handleAction}
                disabled={sending}
                className={`w-full py-2.5 rounded-xl font-semibold text-sm transition-all
                  bg-gradient-to-r from-blue-600 to-indigo-600
                  hover:from-blue-500 hover:to-indigo-500
                  text-white shadow-lg shadow-blue-500/20
                  disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {sending ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="inline-block w-4 h-4 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
                    Processing…
                  </span>
                ) : (
                  buttonText
                )}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
