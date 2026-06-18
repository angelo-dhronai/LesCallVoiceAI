import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import { AuthGate } from './auth.jsx'
import Layout from './components/Layout.jsx'
import Overview from './pages/Overview.jsx'
import Agents from './pages/Agents.jsx'
import Campaigns from './pages/Campaigns.jsx'
import Calls from './pages/Calls.jsx'
import Numbers from './pages/Numbers.jsx'
import Live from './pages/Live.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthGate>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/calls" element={<Calls />} />
          <Route path="/numbers" element={<Numbers />} />
          <Route path="/live" element={<Live />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Route>
      </Routes>
    </BrowserRouter>
    </AuthGate>
  </React.StrictMode>
)
