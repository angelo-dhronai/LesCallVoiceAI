import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

/** Debounce a fast-changing value (search boxes feeding server queries). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

/**
 * A single piece of state mirrored into the URL query string (replace, not
 * push — Back never walks through keystrokes). Values equal to the fallback
 * are removed from the URL to keep it clean. Use one hook per param:
 *
 *   const [search, setSearch] = useUrlParam('q')
 *   const [page, setPage] = useUrlParam('page', '1')
 */
export function useUrlParam(key: string, fallback = ''): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback((v: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (!v || v === fallback) next.delete(key)
      else next.set(key, v)
      return next
    }, { replace: true })
  }, [key, fallback, setParams])
  return [value, set]
}

/** Numeric convenience over useUrlParam (page numbers etc.). */
export function useUrlNumber(key: string, fallback: number): [number, (v: number) => void] {
  const [raw, setRaw] = useUrlParam(key, String(fallback))
  const n = Number.parseInt(raw, 10)
  const value = Number.isFinite(n) && n > 0 ? n : fallback
  const set = useCallback((v: number) => setRaw(String(v)), [setRaw])
  return [value, set]
}
