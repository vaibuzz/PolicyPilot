/**
 * ReviewScreen — Screen 2
 * Shows all extracted rules in ONE unified JSON code block,
 * manages review state, and opens:
 *   - RuleGraphPanel (left)  via "Rule Graph" button in SummaryBar
 *   - SidePanel      (right) via "Test These Rules" button (bottom-right)
 *
 * Rule Graph is only enabled when all flags are resolved.
 * Center content shifts right when left panel is open.
 * On narrow screens (<1024px) opening one panel closes the other.
 */
import React, { useState, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import SummaryBar from '../components/SummaryBar'
import UnifiedRuleBlock from '../components/RuleCard'
import SidePanel from '../components/SidePanel'
import RuleGraphPanel from '../components/RuleGraphPanel'
import { finalizeRules } from '../api/client'

const DEFAULT_PANEL_WIDTH = 448  // must match RuleGraphPanel DEFAULT_WIDTH

export default function ReviewScreen() {
  const location = useLocation()
  const navigate = useNavigate()

  const initialState = location.state || {}
  const [rules, setRules] = useState(initialState.rules || [])
  const conflicts = initialState.conflicts || []
  const summary = initialState.summary || {}
  const fallbackActive = initialState.fallback_active || false

  const [sidePanelOpen, setSidePanelOpen] = useState(false)
  const [graphPanelOpen, setGraphPanelOpen] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [finalizeError, setFinalizeError] = useState('')
  const [finalizeSuccess, setFinalizeSuccess] = useState(false)

  // Redirect if arrived without state
  if (!initialState.rules) {
    navigate('/')
    return null
  }

  function handleRuleUpdate(ruleId, updatedRule) {
    setRules((prev) =>
      prev.map((r) => (r.rule_id === ruleId ? { ...r, ...updatedRule } : r))
    )
  }

  const flaggedRules = rules.filter(r =>
    (r.confidence_score < 0.9 || r.conflict_with?.length > 0) &&
    !['accepted', 'modified', 'kept_original'].includes(r.review_status)
  )

  function scrollToNextFlag() {
    if (flaggedRules.length > 0) {
      const nextRule = flaggedRules[0]
      const el = document.getElementById(`flagged-banner-${nextRule.rule_id}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('bg-amber-900/50')
        setTimeout(() => el.classList.remove('bg-amber-900/50'), 600)
      }
    }
  }

  async function handleFinalize() {
    setFinalizeError('')
    setFinalizing(true)
    try {
      await finalizeRules(rules)
      setFinalizeSuccess(true)
    } catch (e) {
      setFinalizeError(e.response?.data?.detail || e.message || 'Finalization failed.')
    } finally {
      setFinalizing(false)
    }
  }

  // Toggle left panel. On narrow screens, close right panel first.
  function toggleGraphPanel() {
    if (window.innerWidth < 1024 && !graphPanelOpen && sidePanelOpen) {
      setSidePanelOpen(false)
    }
    setGraphPanelOpen((v) => !v)
  }

  // Open right panel. On narrow screens, close left panel first.
  function openSidePanel() {
    if (window.innerWidth < 1024 && graphPanelOpen) {
      setGraphPanelOpen(false)
    }
    setSidePanelOpen(true)
  }

  // Center content shifts right when left panel is open.
  // Use inline style for pixel-perfect sync with resizable panel.
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH)
  const contentShift = graphPanelOpen ? panelWidth : 0

  return (
    <div className="min-h-screen">
      {/* Ambient */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 right-1/3 w-96 h-64 bg-violet-500/3 rounded-full blur-3xl" />
      </div>

      {/* Left panel — passes setPanelWidth so ReviewScreen tracks its width */}
      <RuleGraphPanel
        open={graphPanelOpen}
        onClose={() => setGraphPanelOpen(false)}
        rules={rules}
        onWidthChange={setPanelWidth}
      />

      {/* Main content — shifts right when left panel is open */}
      <div
        className="relative z-10 transition-all duration-300 ease-out"
        style={{ marginLeft: contentShift, minWidth: 0 }}
      >
        <div className="max-w-5xl mx-auto px-4 py-6">

          {/* SummaryBar now owns the Rule Graph button */}
          <SummaryBar
            rules={rules}
            onFinalize={handleFinalize}
            finalizing={finalizing}
            isFinalized={finalizeSuccess}
            onFlaggedClick={scrollToNextFlag}
            onGraphClick={toggleGraphPanel}
            graphPanelOpen={graphPanelOpen}
            fallbackActive={fallbackActive}
          />

          {/* Finalize success / error */}
          {finalizeSuccess && (
            <div className="mb-4 p-3 rounded-lg bg-emerald-950/40 border border-emerald-500/20 text-emerald-300 text-sm animate-fade-in">
              ✓ Ruleset finalized successfully. You can now test documents in the side panel.
            </div>
          )}
          {finalizeError && (
            <div className="mb-4 p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-sm">
              {finalizeError}
            </div>
          )}

          {/* Extraction summary chips */}
          <div className="flex gap-3 mb-6 flex-wrap">
            <span className="text-xs bg-slate-800 border border-slate-700 rounded-full px-3 py-1 text-slate-400">
              {summary.total_rules ?? rules.length} rules extracted
            </span>
            {summary.conflicts_found > 0 && (
              <button
                onClick={scrollToNextFlag}
                className="text-xs bg-amber-950/40 hover:bg-amber-900/50 border border-amber-500/30 rounded-full px-3 py-1 text-amber-400 cursor-pointer transition-colors"
              >
                {summary.conflicts_found} conflict{summary.conflicts_found > 1 ? 's' : ''} detected
              </button>
            )}
            <span className="text-xs bg-emerald-950/30 border border-emerald-500/20 rounded-full px-3 py-1 text-emerald-400">
              {summary.high_confidence ?? 0} high-confidence
            </span>
          </div>

          {/* Single unified JSON code block for all rules */}
          {rules.length > 0 && (
            <UnifiedRuleBlock
              rules={rules}
              conflicts={conflicts}
              onUpdate={handleRuleUpdate}
            />
          )}

          {rules.length === 0 && (
            <div className="text-center py-20 text-slate-500">
              No rules extracted. Go back and upload a policy document.
            </div>
          )}

          {/* Bottom action bar */}
          <div className="fixed bottom-6 right-6 flex gap-3 z-10">
            <button
              onClick={openSidePanel}
              disabled={!finalizeSuccess}
              className={`px-5 py-3 rounded-xl font-semibold text-sm shadow-lg transition-all duration-200 ${
                finalizeSuccess
                  ? 'bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-white shadow-violet-500/20'
                  : 'bg-slate-800 text-slate-500 cursor-not-allowed shadow-none border border-slate-700'
              }`}
            >
              ⚡ Test These Rules
            </button>
          </div>
        </div>
      </div>

      {/* Right panel (overlay, no layout shift) */}
      <SidePanel
        open={sidePanelOpen}
        onClose={() => setSidePanelOpen(false)}
      />
    </div>
  )
}
