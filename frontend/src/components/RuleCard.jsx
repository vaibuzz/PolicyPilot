/**
 * UnifiedRuleBlock — renders ALL rules as ONE continuous JSON code block.
 *
 * The entire rules array is displayed as a single [...] JSON with:
 *   - Continuous line numbers across all rules
 *   - Copy button to copy the clean JSON
 *   - Inline suggestion blocks for flagged rules:
 *       • Red-highlighted lines on differing fields
 *       • Green suggestion preview lines (with line numbers)
 *       • Accept / Edit / Keep buttons
 *   - When a suggestion is acted on, the suggestion lines disappear
 *   - Edit mode shows an inline textarea for the specific rule
 */
import React, { useState, useMemo, useCallback } from 'react'

// ── Helpers ─────────────────────────────────────────────────────────────────

const STANDARD_ACTIONS = [
  'AUTO_APPROVE',
  'ROUTE_TO_AP_CLERK',
  'ROUTE_TO_DEPT_HEAD',
  'ESCALATE_TO_FINANCE_CONTROLLER',
  'ESCALATE_TO_CFO',
  'HOLD',
  'REJECT',
  'FLAG',
  'ROUTE_TO_PROCUREMENT',
  'COMPLIANCE_HOLD',
]

function valuesMatch(a, b) {
  return JSON.stringify(a) === JSON.stringify(b)
}

function getDiffKeys(rule, fix) {
  if (!fix) return new Set()
  const skip = new Set(['conflict_with', 'suggested_fix', 'review_status', 'section'])
  const allKeys = new Set([...Object.keys(rule), ...Object.keys(fix)])
  const diffs = new Set()
  for (const k of allKeys) {
    if (skip.has(k)) continue
    if (!valuesMatch(rule[k], fix[k])) diffs.add(k)
  }
  return diffs
}

function isFlaggedRule(rule) {
  return rule.confidence_score < 0.9 || (rule.conflict_with?.length > 0)
}

function isResolvedRule(rule) {
  return ['accepted', 'modified', 'kept_original'].includes(rule.review_status)
}

function getConflictExplanation(rule, conflicts) {
  if (!conflicts) return ''
  return conflicts
    .filter((c) => c.rule_id_b === rule.rule_id || c.rule_id_a === rule.rule_id)
    .map((c) => c.explanation)
    .join(' ')
}

function syntaxHighlight(text) {
  let s = text
    .replace(/&/g, '&amp;')
    .replace(/</, '&lt;')
    .replace(/>/, '&gt;')

  // JSON key → cyan
  s = s.replace(
    /("(?:[^"\\]|\\.)*")\s*:/g,
    '<span style="color:#67e8f9">$1</span>:'
  )
  // String value → green
  s = s.replace(
    /:\s*("(?:[^"\\]|\\.)*")/g,
    (match, val) => match.replace(val, `<span style="color:#86efac">${val}</span>`)
  )
  // Standalone strings in arrays
  s = s.replace(
    /(?<=[\[,]\s*)("(?:[^"\\]|\\.)*")/g,
    '<span style="color:#86efac">$1</span>'
  )
  // Numbers → amber
  s = s.replace(
    /:\s*(-?\d+\.?\d*)/g,
    (match, num) => match.replace(num, `<span style="color:#fbbf24">${num}</span>`)
  )
  // Booleans / null → purple
  s = s.replace(
    /\b(true|false|null)\b/g,
    '<span style="color:#c084fc">$1</span>'
  )
  return s
}

// ── Main Component ──────────────────────────────────────────────────────────

export default function UnifiedRuleBlock({ rules, conflicts, onUpdate }) {
  const [editingRuleId, setEditingRuleId] = useState(null)
  const [editJson, setEditJson] = useState('')
  const [editError, setEditError] = useState('')
  const [copied, setCopied] = useState(false)
  const [selectedFixActions, setSelectedFixActions] = useState({})

  // ── Copyable clean JSON (entire rules array) ──────────────────────────
  const copyText = useMemo(() => JSON.stringify(rules, null, 2), [rules])

  // ── Build rendering items ─────────────────────────────────────────────
  // Each item has a `type`:
  //   'line'      — numbered code line (rule JSON or suggestion preview)
  //   'banner'    — suggestion explanation (no line number)
  //   'actions'   — Accept / Edit / Keep buttons (no line number)
  //   'edit-zone' — inline textarea for the rule being edited
  const renderItems = useMemo(() => {
    const items = []
    let lineNum = 0

    // Opening bracket
    items.push({ type: 'line', num: ++lineNum, text: '[', hl: false, sug: false })

    rules.forEach((rule, ruleIdx) => {
      // ── Edit zone placeholder ───────────────────────────────────────
      if (editingRuleId === rule.rule_id) {
        items.push({ type: 'edit-zone', ruleId: rule.rule_id })
        return
      }

      // ── Permanent rule header: always shows rule_id + live confidence badge ─
      // This row is NEVER removed — even after Accept/Edit/Keep — so the user
      // can see the confidence score climb after taking action.
      items.push({
        type: 'rule-header',
        ruleId: rule.rule_id,
        confidenceScore: rule.confidence_score ?? 0,
        reviewStatus: rule.review_status,
      })

      // ── Build annotated lines for this rule ─────────────────────────
      const ruleJson = JSON.stringify(rule, null, 2)
      const ruleLines = ruleJson.split('\n')
      const flagged = isFlaggedRule(rule)
      const resolved = isResolvedRule(rule)
      const diffKeys = getDiffKeys(rule, rule.suggested_fix)

      let currentTopKey = null

      ruleLines.forEach((line, lineIdx) => {
        // Track top-level key (2-space indent = top-level inside object)
        const topKeyMatch = line.match(/^  "([^"]+)"/)
        if (topKeyMatch) currentTopKey = topKeyMatch[1]
        const trimmed = line.trim()
        if (trimmed === '{' || trimmed === '}') currentTopKey = null

        // Indent by 2 for array nesting + comma between rules
        let text = '  ' + line
        if (lineIdx === ruleLines.length - 1 && ruleIdx < rules.length - 1) {
          text += ','
        }

        const highlighted =
          flagged && !resolved && currentTopKey != null && diffKeys.has(currentTopKey)

        items.push({ type: 'line', num: ++lineNum, text, hl: highlighted, sug: false })
      })

      // ── Inline suggestion (flagged + unresolved) ────────────────────
      if (flagged && !resolved) {
        const explanation = getConflictExplanation(rule, conflicts)

        let previewText = null
        if (rule.suggested_fix && diffKeys.size > 0) {
          const previewObj = {}
          for (const k of diffKeys) previewObj[k] = rule.suggested_fix[k]
          previewText = JSON.stringify(previewObj, null, 2)
        }

        // Banner (explanation only — confidence badge is now in the permanent header above)
        items.push({
          type: 'banner',
          ruleId: rule.rule_id,
          explanation:
            explanation ||
            (rule.confidence_score < 0.9
              ? `Low confidence (${Math.round(rule.confidence_score * 100)}%). Review condition and action for correctness.`
              : ''),
        })

        // Suggestion preview lines (green bg, numbered)
        if (previewText) {
          previewText.split('\n').forEach((sLine) => {
            items.push({
              type: 'line',
              num: ++lineNum,
              text: '    ' + sLine,
              hl: false,
              sug: true,
            })
          })
        }

        // Action buttons
        const fixAction = rule.suggested_fix?.action
        const isCustomAction = fixAction && !STANDARD_ACTIONS.includes(fixAction)

        items.push({
          type: 'actions',
          ruleId: rule.rule_id,
          hasFix: !!rule.suggested_fix,
          isCustomAction,
          fixAction,
        })
      }
    })

    // Closing bracket
    items.push({ type: 'line', num: ++lineNum, text: ']', hl: false, sug: false })

    return items
  }, [rules, conflicts, editingRuleId])


  // ── Handlers ──────────────────────────────────────────────────────────

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(copyText)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = copyText
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [copyText])

  const handleAccept = useCallback(
    (ruleId) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule?.suggested_fix) return

      let finalAction = rule.suggested_fix.action
      const isCustomAction = finalAction && !STANDARD_ACTIONS.includes(finalAction)
      if (isCustomAction) {
        if (!selectedFixActions[ruleId]) return // MUST select something
        finalAction = selectedFixActions[ruleId]
      }

      // Merge: preserve current fields, override with suggested_fix, clear fix.
      // A human accepted it, so it is now 100% verified.
      onUpdate(ruleId, {
        ...rule,
        ...rule.suggested_fix,
        action: finalAction,
        review_status: 'accepted',
        suggested_fix: null,
        confidence_score: 1.0,
      })
      
      // Clean up selection
      setSelectedFixActions(prev => {
        const next = { ...prev }
        delete next[ruleId]
        return next
      })
    },
    [rules, onUpdate, selectedFixActions]
  )

  const handleEdit = useCallback(
    (ruleId) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      setEditJson(JSON.stringify(rule, null, 2))
      setEditError('')
      setEditingRuleId(ruleId)
    },
    [rules]
  )

  const handleSaveEdit = useCallback(() => {
    try {
      const parsed = JSON.parse(editJson)
      // Manual edit shows human engagement — 100% verified.
      onUpdate(editingRuleId, { ...parsed, review_status: 'modified', confidence_score: 1.0 })
      setEditingRuleId(null)
    } catch (e) {
      setEditError(`Invalid JSON: ${e.message}`)
    }
  }, [editJson, editingRuleId, onUpdate])

  const handleCancelEdit = useCallback(() => {
    setEditingRuleId(null)
    setEditError('')
  }, [])

  const handleKeep = useCallback(
    (ruleId) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      // Human confirmed the original is correct — 100% verified.
      onUpdate(ruleId, { ...rule, review_status: 'kept_original', confidence_score: 1.0 })
    },
    [rules, onUpdate]
  )

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="rounded-xl bg-slate-900/70 backdrop-blur-sm border border-slate-700/50 overflow-hidden">
      {/* ── Header bar with filename + copy button ───────────────────── */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700/40 bg-slate-800/50">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
          </svg>
          <span className="text-xs font-mono text-slate-400">ap_policy_rules.json</span>
          <span className="text-xs text-slate-600">·</span>
          <span className="text-xs text-slate-500">{rules.length} rules</span>
        </div>
        <button
          onClick={handleCopy}
          className={`text-xs px-3 py-1 rounded-md transition-all duration-200 ${
            copied
              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
              : 'bg-slate-700 text-slate-300 hover:bg-slate-600 border border-slate-600'
          }`}
        >
          {copied ? '✓ Copied' : '📋 Copy'}
        </button>
      </div>

      {/* ── Single continuous code block ──────────────────────────────── */}
      <div className="overflow-x-hidden overflow-y-auto max-h-[80vh]">
        <pre className="text-[13px] leading-[1.6] font-mono p-0 m-0 whitespace-pre-wrap break-words">
          <code>
            {renderItems.map((item, idx) => {
              // ── Numbered code line ──────────────────────────────────
              if (item.type === 'line') {
                return (
                  <div
                    key={`l-${idx}`}
                    className={`px-4 ${
                      item.hl
                        ? 'bg-red-500/12'
                        : item.sug
                        ? 'bg-emerald-500/10'
                        : ''
                    }`}
                  >
                    <span className="inline-block w-8 text-right mr-4 text-slate-600 select-none text-[11px]">
                      {item.num}
                    </span>
                    <span
                      dangerouslySetInnerHTML={{
                        __html: syntaxHighlight(item.text),
                      }}
                    />
                  </div>
                )
              }

              // ── Permanent rule header with live confidence badge ────
              if (item.type === 'rule-header') {
                const confPct = Math.round((item.confidenceScore ?? 0) * 100)
                const confColor =
                  confPct >= 90 ? '#34d399'
                  : confPct >= 70 ? '#fbbf24'
                  : '#f87171'
                const confBg =
                  confPct >= 90 ? 'rgba(52,211,153,0.08)'
                  : confPct >= 70 ? 'rgba(251,191,36,0.08)'
                  : 'rgba(248,113,113,0.08)'
                const statusBadge =
                  item.reviewStatus === 'accepted' ? { label: '✓ Accepted', color: '#34d399', bg: 'rgba(52,211,153,0.1)' }
                  : item.reviewStatus === 'modified' ? { label: '✎ Modified', color: '#818cf8', bg: 'rgba(129,140,248,0.1)' }
                  : item.reviewStatus === 'kept_original' ? { label: '◎ Kept', color: '#94a3b8', bg: 'rgba(148,163,184,0.1)' }
                  : null

                return (
                  <div
                    key={`rh-${item.ruleId}`}
                    className="flex items-center gap-2 px-4 py-1.5 border-t border-slate-700/40 bg-slate-800/40 select-none"
                  >
                    {/* Rule ID */}
                    <span className="text-[10px] font-mono text-slate-500 mr-1">{item.ruleId}</span>
                    {/* Live confidence badge */}
                    <span
                      className="text-[10px] font-bold font-mono px-2 py-0.5 rounded-full"
                      style={{
                        color: confColor,
                        background: confBg,
                        border: `1px solid ${confColor}40`,
                        transition: 'all 0.4s ease',
                      }}
                      title="LLM confidence score. Increases when you accept suggestions."
                    >
                      ⬤ {confPct}%
                    </span>
                    {/* Review status badge (shown only after action taken) */}
                    {statusBadge && (
                      <span
                        className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ color: statusBadge.color, background: statusBadge.bg }}
                      >
                        {statusBadge.label}
                      </span>
                    )}
                  </div>
                )
              }

              // ── Suggestion banner (no line number) ─────────────────
              if (item.type === 'banner') {
                // Find the rule to read its live confidence score
                const ruleForBanner = rules.find(r => r.rule_id === item.ruleId)
                const confScore = ruleForBanner?.confidence_score ?? 0
                const confPct = Math.round(confScore * 100)
                const confColor =
                  confPct >= 90 ? '#34d399'
                  : confPct >= 70 ? '#fbbf24'
                  : '#f87171'
                const confBg =
                  confPct >= 90 ? 'rgba(52,211,153,0.12)'
                  : confPct >= 70 ? 'rgba(251,191,36,0.12)'
                  : 'rgba(248,113,113,0.12)'

                return (
                  <div
                    key={`b-${idx}`}
                    id={`flagged-banner-${item.ruleId}`}
                    className="px-4 py-2.5 bg-amber-950/25 border-y border-amber-500/15"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-[11px] font-semibold text-amber-400 tracking-wider uppercase">
                        ── Suggested Fix ({item.ruleId}) ──
                      </span>
                      {/* Per-rule confidence badge */}
                      <span
                        className="text-[10px] font-bold font-mono px-2 py-0.5 rounded-full"
                        style={{ color: confColor, background: confBg, border: `1px solid ${confColor}40` }}
                        title="LLM extraction confidence. Increases when you accept suggestions."
                      >
                        ⬤ {confPct}% confidence
                      </span>
                    </div>
                    {item.explanation && (
                      <p className="text-xs text-amber-200/80 mt-1 leading-relaxed pl-12">
                        ⚠ {item.explanation}
                      </p>
                    )}
                  </div>
                )
              }

              // ── Action buttons (no line number) ────────────────────
              if (item.type === 'actions') {
                const needsDropdown = item.hasFix && item.isCustomAction
                const selectedAction = selectedFixActions[item.ruleId] || ''
                const canAccept = item.hasFix && (!needsDropdown || selectedAction)

                return (
                  <div
                    key={`a-${idx}`}
                    className="px-4 py-2.5 bg-slate-800/30 border-b border-slate-700/30 flex items-center gap-4 pl-16 flex-wrap"
                  >
                    {needsDropdown && (
                      <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 px-3 py-1.5 rounded-lg">
                        <span className="text-xs font-semibold text-red-400 uppercase tracking-wide">
                          Custom Action (Needs Review):
                        </span>
                        <span className="text-xs text-slate-300 font-mono line-through opacity-70">
                          {item.fixAction}
                        </span>
                        <select
                          value={selectedAction}
                          onChange={(e) =>
                            setSelectedFixActions((prev) => ({ ...prev, [item.ruleId]: e.target.value }))
                          }
                          className="ml-2 bg-slate-900 border border-slate-600 text-slate-200 text-xs rounded px-2 py-1 focus:outline-none focus:border-cyan-500"
                        >
                          <option value="" disabled>Select Standard Action...</option>
                          {STANDARD_ACTIONS.map((a) => (
                            <option key={a} value={a}>
                              {a}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                    
                    <div className="flex items-center gap-2">
                      {item.hasFix && (
                        <button
                          onClick={() => handleAccept(item.ruleId)}
                          disabled={!canAccept}
                          className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                            canAccept 
                              ? 'bg-emerald-600 hover:bg-emerald-500 text-white' 
                              : 'bg-emerald-600/30 text-emerald-100/50 cursor-not-allowed'
                          }`}
                        >
                          ✓ Accept Suggestion
                        </button>
                      )}
                      <button
                        onClick={() => handleEdit(item.ruleId)}
                        className="px-3 py-1 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-medium transition-colors"
                      >
                        ✎ Edit
                      </button>
                      <button
                        onClick={() => handleKeep(item.ruleId)}
                        className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors border ${
                          !item.hasFix 
                            ? 'bg-emerald-600 hover:bg-emerald-500 text-white border-emerald-500' 
                            : 'bg-slate-800 hover:bg-slate-700 text-slate-400 border-slate-600'
                        }`}
                      >
                        Keep Original
                      </button>
                    </div>
                  </div>
                )
              }

              // ── Inline edit zone ───────────────────────────────────
              if (item.type === 'edit-zone') {
                return (
                  <div
                    key={`e-${idx}`}
                    className="px-4 py-3 bg-slate-800/50 border-y border-cyan-500/20"
                  >
                    <div className="flex items-center gap-2 mb-2 pl-12">
                      <span className="text-xs font-mono text-cyan-400">
                        {item.ruleId}
                      </span>
                      <span className="text-xs text-slate-500">— editing</span>
                    </div>
                    <textarea
                      className="w-full h-64 bg-slate-950 text-slate-100 font-mono text-[13px] leading-[1.6] p-4 rounded-lg border border-slate-600 focus:outline-none focus:border-cyan-500 resize-y"
                      value={editJson}
                      onChange={(e) => setEditJson(e.target.value)}
                      spellCheck={false}
                    />
                    {editError && (
                      <p className="text-red-400 text-xs mt-1 pl-12">
                        {editError}
                      </p>
                    )}
                    <div className="flex gap-2 mt-2 pl-12">
                      <button
                        onClick={handleSaveEdit}
                        className="px-4 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-medium transition-colors"
                      >
                        Save Changes
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="px-4 py-1.5 rounded-lg bg-slate-700 text-slate-400 text-xs transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )
              }

              return null
            })}
          </code>
        </pre>
      </div>
    </div>
  )
}
