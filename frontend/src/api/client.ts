const BASE = '/api'

export async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers || {}) },
    ...opts,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json()
  return (await res.text()) as unknown as T
}

export function sse(path: string, onMessage: (data: any) => void): () => void {
  const es = new EventSource(`${BASE}${path}`)
  es.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)) } catch { onMessage(e.data) }
  }
  return () => es.close()
}
