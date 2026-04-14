/**
 * RuleGraphPanel — Left-side drawer showing the Visual Rule Graph.
 *
 * Features:
 *  - Mirrors SidePanel.jsx: fixed top-0 left-0, slides from left
 *  - Resizable via drag handle on right edge (min 320px, max 50vw)
 *  - Mermaid 10.6.1 from CDN, dark theme, larger legible text
 *  - Debounced 500ms re-render on rules change
 *  - Graceful error display, spinner while loading
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { generateRuleGraph, getActiveRules } from '../api/client'

// ── Mermaid CDN ──────────────────────────────────────────────────────────────

const MERMAID_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js'
let _mermaidPromise = null

function loadMermaid() {
  if (_mermaidPromise) return _mermaidPromise
  _mermaidPromise = new Promise((resolve, reject) => {
    if (window.mermaid) { resolve(window.mermaid); return }
    const s = document.createElement('script')
    s.src = MERMAID_CDN
    s.onload = () => {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        themeCSS: `
          .edgeLabel { 
            background-color: #cbd5e1 !important; 
            color: #0f172a !important; 
            font-size: 14px !important; 
            font-weight: 700 !important; 
            padding: 4px 8px !important; 
            border-radius: 4px !important; 
          }
          .node rect, .node polygon, .node circle, .node ellipse {
            rx: 6px !important;
            ry: 6px !important;
          }
          .node {
            cursor: pointer;
          }
        `,
        flowchart: {
          useMaxWidth: true,
          htmlLabels: true,
          curve: 'basis',
          nodeSpacing: 50,
          rankSpacing: 60,
        },
        themeVariables: {
          fontSize: '16px',
          fontFamily: 'Inter, system-ui, sans-serif',
          primaryColor: '#3730a3',
          primaryTextColor: '#f8fafc',
          primaryBorderColor: '#6366f1',
          lineColor: '#94a3b8',
          secondaryColor: '#1e1b4b',
          tertiaryColor: '#0f172a',
          edgeLabelBackground: '#cbd5e1',
          clusterBkg: '#1e293b',
        },
      })
      resolve(window.mermaid)
    }
    s.onerror = () => reject(new Error('Mermaid CDN failed to load'))
    document.head.appendChild(s)
  })
  return _mermaidPromise
}

// ── Graph icon ───────────────────────────────────────────────────────────────

export function GraphIcon({ className = 'w-4 h-4' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="5"  cy="12" r="2" />
      <circle cx="19" cy="5"  r="2" />
      <circle cx="19" cy="19" r="2" />
      <line x1="7"  y1="11" x2="17" y2="6"  />
      <line x1="7"  y1="13" x2="17" y2="18" />
    </svg>
  )
}

// ── Spinner ──────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20">
      <div className="relative w-12 h-12">
        <span className="absolute inset-0 rounded-full border-2 border-violet-500/20" />
        <span className="absolute inset-0 rounded-full border-2 border-t-violet-400 border-r-transparent border-b-transparent border-l-transparent animate-spin" />
      </div>
      <p className="text-sm text-slate-400 font-medium">Generating graph…</p>
      <p className="text-xs text-slate-600">Calling backend + rendering SVG</p>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

const MIN_WIDTH = 320
const DEFAULT_WIDTH = 448  // 28rem — same as before

export default function RuleGraphPanel({ open, onClose, rules, onWidthChange }) {
  const [svgContent, setSvgContent] = useState('')
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)

  const debounceRef  = useRef(null)
  const renderIdRef  = useRef(0)
  const isDragging   = useRef(false)
  const dragStartX   = useRef(0)
  const dragStartW   = useRef(DEFAULT_WIDTH)

  // ── Graph render ─────────────────────────────────────────────────────────

  const renderGraph = useCallback(async () => {
    setLoading(true); setError('')
    const id = ++renderIdRef.current
    try {
      const activeRes = await getActiveRules()
      const finalizedRules = activeRes.data.rules
      if (!finalizedRules || finalizedRules.length === 0) {
        if (id !== renderIdRef.current) return
        setSvgContent(''); setError('No active rules found. Please finalize rules first.')
        return
      }
      
      const res = await generateRuleGraph(finalizedRules)
      const mermaidStr = res.data.mermaid
      if (id !== renderIdRef.current) return
      const mermaid = await loadMermaid()
      if (id !== renderIdRef.current) return
      const uid = `rg-${Date.now()}-${id}`
      const { svg } = await mermaid.render(uid, mermaidStr)
      if (id !== renderIdRef.current) return
      setSvgContent(svg)
    } catch (e) {
      if (id !== renderIdRef.current) return
      setError(e?.response?.data?.detail || e?.message || 'Render failed.')
      setSvgContent('')
    } finally {
      if (id === renderIdRef.current) setLoading(false)
    }
  }, [])

  // Debounce re-render when panel is open
  useEffect(() => {
    if (!open) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => renderGraph(), 500)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [open, renderGraph])

  // ── Resize drag handle ───────────────────────────────────────────────────

  const maxWidth = typeof window !== 'undefined' ? Math.floor(window.innerWidth * 0.5) : 700

  function onDragStart(e) {
    isDragging.current = true
    dragStartX.current = e.clientX
    dragStartW.current = panelWidth
    document.body.style.cursor    = 'ew-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    function onMouseMove(e) {
      if (!isDragging.current) return
      const delta = e.clientX - dragStartX.current
      const newW  = Math.min(maxWidth, Math.max(MIN_WIDTH, dragStartW.current + delta))
      setPanelWidth(newW)
      if (onWidthChange) onWidthChange(newW)
    }
    function onMouseUp() {
      if (!isDragging.current) return
      isDragging.current            = false
      document.body.style.cursor    = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup',   onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup',   onMouseUp)
    }
  }, [maxWidth])

  return (
    <>
      {/* Backdrop on narrow screens */}
      {open && (
        <div
          className="fixed inset-0 bg-black/30 backdrop-blur-sm z-20 xl:hidden"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={`fixed top-0 left-0 h-full bg-slate-900 border-r border-slate-700/50 z-30 flex flex-col shadow-2xl transition-transform duration-300 ${
          open ? 'translate-x-0 animate-slide-in-left' : '-translate-x-full'
        }`}
        style={{ width: panelWidth }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/50 bg-slate-900 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-violet-600/20 border border-violet-500/30 flex items-center justify-center">
              <GraphIcon className="w-4 h-4 text-violet-400" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-white tracking-wide">Rule Graph</h2>
              <p className="text-xs text-slate-500 mt-0.5">Decision tree · {rules?.length ?? 0} rules</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Width indicator */}
            <span className="text-xs text-slate-600 font-mono hidden sm:inline">
              {panelWidth}px
            </span>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto overflow-x-auto p-4">
          {loading && <Spinner />}

          {!loading && error && (
            <div className="mt-4 text-sm bg-red-950/30 border border-red-500/20 rounded-xl p-4">
              <p className="font-semibold text-red-400 mb-1">⚠ Graph error</p>
              <p className="text-xs text-red-300/80">{error}</p>
            </div>
          )}

          {!loading && !error && svgContent && (
            <div
              className="animate-fade-in"
              style={{
                /* Scale the SVG to fill the panel width while keeping readability */
                width: '100%',
                overflowX: 'auto',
              }}
              dangerouslySetInnerHTML={{ __html: svgContent }}
            />
          )}

          {!loading && !error && !svgContent && (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-slate-600">
              <GraphIcon className="w-10 h-10 opacity-30" />
              <p className="text-sm">Graph will appear here</p>
            </div>
          )}
        </div>

        {/* Drag handle — right edge */}
        <div
          onMouseDown={onDragStart}
          className="absolute top-0 right-0 w-1.5 h-full cursor-ew-resize group z-40"
          title="Drag to resize"
        >
          {/* Visible track */}
          <div className="absolute inset-y-0 right-0 w-1 bg-slate-700/50 group-hover:bg-violet-500/60 transition-colors duration-150" />
          {/* Grip dots */}
          <div className="absolute top-1/2 -translate-y-1/2 right-0 flex flex-col gap-1 pr-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            {[0,1,2].map(i => (
              <div key={i} className="w-1 h-1 rounded-full bg-violet-400" />
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
