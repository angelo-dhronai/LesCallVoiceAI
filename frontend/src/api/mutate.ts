import { api } from './client'

/** Thin helpers around the JSON `api()` fetcher for write operations. */
export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body == null ? undefined : JSON.stringify(body) })

export const patch = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PATCH', body: body == null ? undefined : JSON.stringify(body) })

export const del = <T = any>(path: string) => api<T>(path, { method: 'DELETE' })

/** Multipart upload (no JSON Content-Type so the browser sets the boundary). */
export async function postForm<T = any>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
