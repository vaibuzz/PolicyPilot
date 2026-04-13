import React from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import UploadScreen from './screens/UploadScreen'
import ReviewScreen from './screens/ReviewScreen'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<UploadScreen />} />
        <Route path="/review" element={<ReviewScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
