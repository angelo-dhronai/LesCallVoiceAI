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
