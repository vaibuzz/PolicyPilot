/**
 * SummaryBar — top bar showing stats, overall confidence meter, and Finalize button.
 * Finalize is disabled until all flagged rules are actioned.
 */
import React, { useMemo } from 'react'

function Stat({ label, value, color = 'text-slate-200' }) {
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-0.5">{label}</div>
    </div>
  )
}

/** Animated SVG arc ring showing 0-100% confidence */
function ConfidenceRing({ score }) {
  const pct = Math.round((score ?? 0) * 100)
  const radius = 28
  const circumference = 2 * Math.PI * radius
  const filled = (pct / 100) * circumference

  const color =
    pct >= 90 ? '#34d399'  // emerald
    : pct >= 70 ? '#fbbf24' // amber
    : '#f87171'              // red

  const label =
    pct >= 90 ? 'High Confidence'
    : pct >= 70 ? 'Medium — Review Flags'
    : 'Low — Do Not Finalize'

  return (
    <div className="flex items-center gap-3">
      <div className="relative w-16 h-16 flex-shrink-0">
        <svg viewBox="0 0 72 72" className="w-full h-full -rotate-90">
          {/* Background track */}
          <circle
            cx="36" cy="36" r={radius}
            fill="none"
            stroke="#1e293b"
            strokeWidth="6"
          />
          {/* Filled arc */}
          <circle
            cx="36" cy="36" r={radius}
            fill="none"
            stroke={color}
            strokeWidth="6"
            strokeDasharray={`${filled} ${circumference}`}
            strokeLinecap="round"
            style={{ transition: 'stroke-dasharray 0.5s ease, stroke 0.4s ease' }}
          />
        </svg>
        {/* Percentage label in centre */}
        <div
          className="absolute inset-0 flex items-center justify-center text-xs font-bold font-mono"
          style={{ color }}
        >
          {pct}%
        </div>
      </div>
      <div>
        <div className="text-xs text-slate-400 font-medium">Policy Confidence</div>
        <div className="text-xs mt-0.5 font-semibold" style={{ color }}>{label}</div>
      </div>
    </div>
  )
}

export default function SummaryBar({ rules, onFinalize, finalizing, onFlaggedClick }) {
  const total = rules.length
  const flagged = rules.filter(
    (r) => r.confidence_score < 0.9 || r.conflict_with?.length > 0
  ).length
  const resolved = rules.filter((r) =>
    ['accepted', 'modified', 'kept_original'].includes(r.review_status)
  ).length
  const lowConfidence = rules.filter((r) => r.confidence_score < 0.7).length

  const unresolvedFlags = rules.filter(
    (r) => (r.confidence_score < 0.9 || r.conflict_with?.length > 0) &&
    !['accepted', 'modified', 'kept_original'].includes(r.review_status)
  ).length

  // Overall average confidence across all rules (updates live as user accepts/rejects).
  // Applies a 50% penalty to rules that are flagged but unresolved, so the policy 
  // confidence visibly drops when there are conflicts, and climbs steadily to 100%
  // as the human resolves them.
  const avgConfidence = useMemo(() => {
    if (!rules.length) return 0
    const sum = rules.reduce((acc, r) => {
      let score = r.confidence_score ?? 0
      const isFlagged = r.confidence_score < 0.9 || r.conflict_with?.length > 0
      const isResolved = ['accepted', 'modified', 'kept_original'].includes(r.review_status)
      if (isFlagged && !isResolved) {
        score = score * 0.5 // Treat unresolved flags as low confidence (penalty)
      }
      return acc + score
    }, 0)
    return sum / rules.length
  }, [rules])

  const allFlaggedResolved = unresolvedFlags === 0
  const canFinalize = allFlaggedResolved && total > 0

  return (
    <div className="glass-card px-6 py-4 flex items-center justify-between gap-6 flex-wrap mb-6 sticky top-0 z-10">
      <div className="flex items-center gap-1.5">
        <span className="text-lg font-bold text-white">PolicyPilot</span>
        <span className="text-slate-500 text-lg font-light mx-1">/</span>
        <span className="text-slate-400 text-sm">Rule Review</span>
      </div>

      <div className="flex items-center gap-8">
        <Stat label="Total Rules" value={total} color="text-white" />
        <Stat
          label="Flagged"
          value={flagged}
          color={flagged > 0 ? 'text-amber-400' : 'text-slate-400'}
        />
        <Stat
          label="Resolved"
          value={resolved}
          color={resolved > 0 ? 'text-emerald-400' : 'text-slate-400'}
        />
        <Stat
          label="Low Confidence"
          value={lowConfidence}
          color={lowConfidence > 0 ? 'text-red-400' : 'text-slate-400'}
        />
      </div>

      {/* Overall confidence ring — live-updating */}
      <ConfidenceRing score={avgConfidence} />

      <div className="flex items-center gap-4">
        <button
          onClick={onFlaggedClick}
          disabled={unresolvedFlags === 0}
          className={`px-5 py-2.5 rounded-xl font-semibold text-sm transition-all duration-300 flex items-center gap-2 ${
            unresolvedFlags > 0
              ? 'bg-amber-500/20 text-amber-400 border border-amber-500/60 shadow-[0_0_15px_rgba(245,158,11,0.5)] hover:bg-amber-500/30'
              : 'bg-slate-800 text-slate-500 border border-slate-700 cursor-not-allowed opacity-50'
          }`}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
          Next Flag ({unresolvedFlags})
        </button>

        <button
          onClick={onFinalize}
          disabled={!canFinalize || finalizing}
          className={`px-6 py-2.5 rounded-xl font-semibold text-sm transition-all duration-200 ${
            canFinalize && !finalizing
              ? 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-500/20'
              : 'bg-slate-800 text-slate-600 cursor-not-allowed'
          }`}
        >
          {finalizing ? (
            <span className="flex items-center gap-2">
              <span className="inline-block w-4 h-4 border-2 border-slate-500 border-t-white rounded-full animate-spin" />
              Finalizing…
            </span>
          ) : (
            `Finalize Rules ${unresolvedFlags > 0 ? `(${unresolvedFlags} pending)` : ''}`
          )}
        </button>
      </div>
    </div>
  )
}
