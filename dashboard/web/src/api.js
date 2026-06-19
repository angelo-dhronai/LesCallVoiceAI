// Thin fetch wrapper around the dashboard API. All calls are same-origin /api/*
// (Vite proxies to :8080 in dev; nginx proxies in prod).

async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`/api${path}`, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  get: (p) => req('GET', p),
  post: (p, b) => req('POST', p, b),
  put: (p, b) => req('PUT', p, b),
  del: (p) => req('DELETE', p),

  // auth
  authConfig: () => req('GET', '/auth/config'),
  me: () => req('GET', '/auth/me'),
  logout: () => req('POST', '/auth/logout'),

  // domain helpers
  agents: () => req('GET', '/agents'),
  createAgent: (a) => req('POST', '/agents', a),
  updateAgent: (id, a) => req('PUT', `/agents/${id}`, a),
  deleteAgent: (id) => req('DELETE', `/agents/${id}`),

  campaigns: () => req('GET', '/campaigns'),
  createCampaign: (c) => req('POST', '/campaigns', c),
  campaignStatus: (id, status) => req('POST', `/campaigns/${id}/status?status=${status}`),
  leads: (id) => req('GET', `/campaigns/${id}/leads`),
  addLeads: (id, leads) => req('POST', `/campaigns/${id}/leads`, leads),

  calls: (limit = 100) => req('GET', `/calls?limit=${limit}`),
  transcript: (connId) => req('GET', `/calls/${connId}/transcript`),
  summary: (connId) => req('POST', `/calls/${connId}/summary`),
  analytics: () => req('GET', '/analytics'),
  // Same-origin URL for the recorded WAV (used as <audio src> / download link).
  audioUrl: (connId, download = false) =>
    `/api/calls/${encodeURIComponent(connId)}/audio${download ? '?download=1' : ''}`,

  // knowledge base
  knowledge: () => req('GET', '/knowledge'),
  deleteDocument: (id) => req('DELETE', `/knowledge/${id}`),
  documentUrl: (id) => `/api/knowledge/${id}/download`,
  uploadDocument: (formData) =>
    fetch('/api/knowledge/upload', { method: 'POST', body: formData }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text().catch(() => '')}`)
      return r.json()
    }),

  dialCampaign: (id) => req('POST', `/campaigns/${id}/dial`),
  dialLead: (id) => req('POST', `/leads/${id}/dial`),
  disposeLead: (id, disposition) => req('POST', `/leads/${id}/dispose?disposition=${encodeURIComponent(disposition)}`),

  serverInfo: () => req('GET', '/telephony/server-info'),
  trunks: () => req('GET', '/trunks'),
  createTrunk: (t) => req('POST', '/trunks', t),
  trunkInstructions: (id) => req('GET', `/trunks/${id}/instructions`),
  applyTrunk: (id) => req('POST', `/trunks/${id}/apply`),
  numbers: () => req('GET', '/numbers'),
  createNumber: (n) => req('POST', '/numbers', n),
  deleteNumber: (id) => req('DELETE', `/numbers/${id}`),
}

export function liveSocket(onMsg) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/live/ws`)
  ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data)) } catch {} }
  return ws
}
