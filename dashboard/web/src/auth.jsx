import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api.js'
import LoginLanding from './components/auth/LoginLanding.jsx'

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

// Derive avatar initials + a default workspace label from the user's identity.
export function deriveIdentity(user) {
  const email = user?.email || 'user@local'
  const name = user?.name || email.split('@')[0]
  const initials = name.split(/[\s._-]+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0]?.toUpperCase()).join('') || email[0].toUpperCase()
  const domain = email.split('@')[1] || 'workspace'
  const workspace = domain.split('.')[0].replace(/^\w/, (c) => c.toUpperCase())
  return { email, name, initials, picture: user?.picture || null, workspace }
}

// Gates the app: when Google OAuth is configured and the user isn't logged in,
// shows a sign-in screen. When auth isn't configured, the API returns a default
// user and the app renders normally (single-tenant / dev). The resolved user is
// published via AuthCtx so the whole UI renders that tenant's identity + data.
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
    return <div className="min-h-screen grid place-items-center bg-slate-50 dark:bg-slate-950 text-slate-400">Loading…</div>
  }

  if (state.authEnabled && !state.user) {
    return <LoginLanding />
  }

  return <AuthCtx.Provider value={state.user}>{children}</AuthCtx.Provider>
}
