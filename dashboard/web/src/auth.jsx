import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api.js'

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

// Gates the app: when Google OAuth is configured and the user isn't logged in,
// shows a sign-in screen. When auth isn't configured, the API returns a default
// user and the app renders normally (single-tenant / dev).
export function AuthGate({ children }) {
  const [state, setState] = useState({ loading: true, user: null, authEnabled: false })

  useEffect(() => {
    (async () => {
      let authEnabled = false
      try { authEnabled = (await api.authConfig()).auth_enabled } catch {}
      let user = null
      try { user = await api.me() } catch {}
      setState({ loading: false, user, authEnabled })
    })()
  }, [])

  if (state.loading) {
    return <div className="min-h-screen grid place-items-center text-slate-400">Loading…</div>
  }

  if (state.authEnabled && !state.user) {
    return (
      <div className="min-h-screen grid place-items-center bg-slate-900">
        <div className="card text-center max-w-sm w-full">
          <div className="text-2xl font-bold mb-1">lescall</div>
          <div className="text-sm text-slate-500 mb-6">Voice AI Dashboard</div>
          <a className="btn btn-primary w-full inline-block" href="/api/auth/login">
            Sign in with Google
          </a>
        </div>
      </div>
    )
  }

  return <AuthCtx.Provider value={state.user}>{children}</AuthCtx.Provider>
}
