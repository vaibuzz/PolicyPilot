/**
 * UploadScreen — Screen 1
 * Drag-and-drop zone for policy document upload.
 * Calls /upload-document then /extract-rules with a single loading state.
 */
import React, { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadDocument, extractRules } from '../api/client'

const ACCEPTED_TYPES = ['.pdf', '.md', '.txt']
const ACCEPTED_MIME = ['application/pdf', 'text/markdown', 'text/plain']

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

export default function UploadScreen() {
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef()
  const navigate = useNavigate()

  const handleFile = useCallback((f) => {
    if (!f) return
    const ext = '.' + f.name.split('.').pop().toLowerCase()
    if (!ACCEPTED_TYPES.includes(ext)) {
      setError(`Unsupported file type. Please upload ${ACCEPTED_TYPES.join(', ')}`)
      return
    }
    setError('')
    setFile(f)
  }, [])

  const onDrop = useCallback((e) => {
    e.preventDefault()
    setDragging(false)
    handleFile(e.dataTransfer.files[0])
  }, [handleFile])

  const onDragOver = (e) => { e.preventDefault(); setDragging(true) }
  const onDragLeave = () => setDragging(false)

  async function handleUpload() {
    if (!file) return
    setLoading(true)
    setError('')

    try {
      // Step 1 — upload and convert to Markdown
      const ingestionRes = await uploadDocument(file)
      const { markdown } = ingestionRes.data

      // Step 2 — extract rules + detect conflicts (single loading state)
      const extractionRes = await extractRules(markdown)
      const { rules, conflicts, summary } = extractionRes.data

      navigate('/review', { state: { rules, conflicts, summary } })
    } catch (e) {
      const msg = e.response?.data?.detail || e.message || 'Upload failed. Please try again.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-16">
      {/* Ambient gradient */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-1/4 left-1/3 w-96 h-96 bg-cyan-500/5 rounded-full blur-3xl" />
        <div className="absolute bottom-1/4 right-1/4 w-80 h-80 bg-violet-500/5 rounded-full blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-xl">
        {/* Brand */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-cyan-500 to-blue-600 mb-4 shadow-lg shadow-cyan-500/20">
            <svg className="w-7 h-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-white">PolicyPilot</h1>
          <p className="text-slate-400 mt-2 text-sm leading-relaxed">
            Upload your AP policy document to extract and execute business rules automatically
          </p>
        </div>

        {/* Drop zone */}
        <div
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onClick={() => inputRef.current?.click()}
          className={`glass-card p-10 flex flex-col items-center justify-center gap-4 cursor-pointer transition-all duration-200
            ${dragging ? 'border-cyan-500 bg-cyan-950/20 scale-[1.02]' : 'hover:border-slate-500'}
            ${file ? 'border-emerald-500/40 bg-emerald-950/10' : ''}
          `}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.md,.txt"
            className="hidden"
            onChange={(e) => handleFile(e.target.files[0])}
          />

          {!file ? (
            <>
              <div className="w-16 h-16 rounded-2xl bg-slate-800 border border-slate-700 flex items-center justify-center">
                <svg className="w-8 h-8 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
              </div>
              <div className="text-center">
                <p className="text-slate-300 font-medium">
                  Drop your AP policy here
                </p>
                <p className="text-slate-500 text-sm mt-1">
                  PDF, Markdown, or plain text · Drag & drop or click to browse
                </p>
              </div>
              <div className="flex gap-2">
                {ACCEPTED_TYPES.map((ext) => (
                  <span key={ext} className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                    {ext}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="w-14 h-14 rounded-2xl bg-emerald-950/50 border border-emerald-500/30 flex items-center justify-center">
                <svg className="w-7 h-7 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
              </div>
              <div className="text-center">
                <p className="text-emerald-300 font-medium">{file.name}</p>
                <p className="text-slate-400 text-sm mt-0.5">{formatBytes(file.size)}</p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); setFile(null) }}
                className="text-xs text-slate-500 hover:text-slate-400 underline"
              >
                Remove
              </button>
            </>
          )}
        </div>

        {/* Error */}
        {error && (
          <div className="mt-4 p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* Upload button */}
        <button
          onClick={handleUpload}
          disabled={!file || loading}
          className={`mt-5 w-full py-3.5 rounded-xl font-semibold text-sm transition-all duration-200 ${
            file && !loading
              ? 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-500/20'
              : 'bg-slate-800 text-slate-600 cursor-not-allowed'
          }`}
        >
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <span className="inline-block w-5 h-5 border-2 border-slate-400 border-t-white rounded-full animate-spin" />
              Extracting and analyzing rules…
            </span>
          ) : (
            'Extract & Analyze Rules'
          )}
        </button>

        <p className="text-center text-slate-600 text-xs mt-4">
          Powered by Claude Sonnet · Rules are extracted via two sequential AI calls
        </p>
      </div>
    </div>
  )
}
