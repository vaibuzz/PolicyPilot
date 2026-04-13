/**
 * SidePanel — right-side drawer for document upload and rule testing.
 * Slides in over the ReviewScreen without navigation.
 */
import React, { useState, useRef } from 'react'
import { extractDocuments, executeRules, sendReport } from '../api/client'

const STATUS_CONFIG = {
  PASS:    { label: 'Pass',    cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
  VIOLATION:    { label: 'Violation',    cls: 'bg-red-500/20 text-red-300 border-red-500/30' },
  SKIPPED: { label: 'Skipped', cls: 'bg-slate-500/20 text-slate-400 border-slate-500/30' },
}

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
            <p className="mt-1 text-amber-400">→ Action: {result.action}</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function SidePanel({ open, onClose }) {
  const [files, setFiles] = useState({ invoice: null, po: null, grn: null })
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingMsg, setLoadingMsg] = useState('')
  const [results, setResults] = useState(null)
  const [payload, setPayload] = useState(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)

  function setFile(key) {
    return (file) => setFiles((f) => ({ ...f, [key]: file }))
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

  async function handleSendReport() {
    if (!email) { setError('Enter a recipient email.'); return }
    if (!results) return
    setSending(true)
    setError('')
    try {
      const invoiceNum = payload?.Invoice_table?.invoice_number ?? null
      await sendReport(results, email, invoiceNum)
      setSent(true)
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to send report.')
    } finally {
      setSending(false)
    }
  }

  const passed  = results?.filter((r) => r.status === 'PASS').length ?? 0
  const failed  = results?.filter((r) => r.status === 'VIOLATION').length ?? 0
  const skipped = results?.filter((r) => r.status === 'SKIPPED').length ?? 0

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-20"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 right-0 h-full w-full max-w-lg bg-slate-900 border-l border-slate-700/50 z-30 overflow-y-auto shadow-2xl transition-transform duration-300 ${
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

          {/* Email */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">
              Report Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="ap-team@company.com"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500 transition-colors"
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
                {failed === 0 ? '✓ COMPLIANT' : `✗ NON-COMPLIANT — ${failed} violation${failed > 1 ? 's' : ''}`}
              </div>

              {/* Rule rows */}
              <div className="space-y-2">
                {results.map((r) => (
                  <ResultRow key={r.rule_id} result={r} />
                ))}
              </div>

              {/* Send report */}
              <button
                onClick={handleSendReport}
                disabled={sending || sent}
                className={`w-full py-2.5 rounded-xl font-semibold text-sm transition-all ${
                  sent
                    ? 'bg-emerald-900/40 text-emerald-400 border border-emerald-500/30'
                    : 'bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-600'
                } disabled:opacity-50`}
              >
                {sent ? '✓ Report Sent' : sending ? 'Sending…' : '📧 Send Report'}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
