/**
 * DiffView — shows field-level diff between current rule and suggested_fix.
 * Only fields that differ are shown.
 * Red block = current value. Green block = suggested value.
 */
import React from 'react'

const SKIP_FIELDS = ['conflict_with', 'suggested_fix', 'review_status']

function renderValue(val) {
  if (val === null || val === undefined) return 'null'
  if (typeof val === 'object') return JSON.stringify(val, null, 2)
  return String(val)
}

function getDiffedFields(current, suggested) {
  const allKeys = new Set([
    ...Object.keys(current || {}),
    ...Object.keys(suggested || {}),
  ])
  const diffs = []
  for (const key of allKeys) {
    if (SKIP_FIELDS.includes(key)) continue
    const a = renderValue(current?.[key])
    const b = renderValue(suggested?.[key])
    if (a !== b) {
      diffs.push({ key, current: a, suggested: b })
    }
  }
  return diffs
}

export default function DiffView({ currentRule, suggestedFix, explanation }) {
  if (!suggestedFix) return null

  const diffs = getDiffedFields(currentRule, suggestedFix)

  if (diffs.length === 0) {
    return (
      <div className="mt-3 p-3 rounded-lg bg-slate-800/50 text-slate-400 text-sm">
        Suggested fix has no field-level differences from the current rule.
      </div>
    )
  }

  return (
    <div className="mt-4 space-y-4 animate-fade-in">
      {/* Explanation */}
      {explanation && (
        <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-500/20 text-amber-200 text-sm leading-relaxed">
          <span className="font-semibold text-amber-400">⚠ Conflict detected: </span>
          {explanation}
        </div>
      )}

      {/* Field diffs */}
      {diffs.map(({ key, current, suggested }) => (
        <div key={key} className="space-y-1">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">
            {key}
          </p>

          {/* Current (remove) */}
          <div className="diff-remove rounded-r-lg overflow-x-auto">
            <span className="text-red-400 mr-2 select-none">−</span>
            <pre className="inline whitespace-pre-wrap break-all">{current}</pre>
          </div>

          {/* Suggested (add) */}
          <div className="diff-add rounded-r-lg overflow-x-auto">
            <span className="text-emerald-400 mr-2 select-none">+</span>
            <pre className="inline whitespace-pre-wrap break-all">{suggested}</pre>
          </div>
        </div>
      ))}
    </div>
  )
}
