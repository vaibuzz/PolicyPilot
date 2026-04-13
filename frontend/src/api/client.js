import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: BASE,
  timeout: 120000, // 2 min — Claude calls can be slow
})

export const uploadDocument = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/upload-document', form)
}

export const extractRules = (markdown) =>
  api.post('/extract-rules', { markdown })

export const finalizeRules = (rules) =>
  api.post('/finalize-rules', { rules })

export const getActiveRules = () =>
  api.get('/active-rules')

export const extractDocuments = (files) => {
  const form = new FormData()
  if (files.invoice) form.append('invoice', files.invoice)
  if (files.po)      form.append('po',      files.po)
  if (files.grn)     form.append('grn',     files.grn)
  return api.post('/extract-documents', form)
}

export const executeRules = (payload) =>
  api.post('/execute-rules', { payload })

export const sendReport = (executionResults, email, invoiceNumber) =>
  api.post('/send-report', {
    execution_results: executionResults,
    email,
    invoice_number: invoiceNumber ?? null,
  })
