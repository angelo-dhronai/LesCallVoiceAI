import { PhoneCall, Mic, Languages, ShieldCheck, BarChart3 } from 'lucide-react'

// Sign-in landing page. Content mirrors lescall.dhronai.com
// ("AI Voice Agents for Real Phone Calls") in the LesCall brand palette
// (deep navy hero + green/blue accents).
const FEATURES = [
  { icon: PhoneCall, title: 'Real phone calls', body: 'Inbound & outbound voice agents over real telephony — not a web widget.' },
  { icon: Languages, title: 'Speaks their language', body: 'Detects and replies in English, Hindi & Gujarati, mid-call.' },
  { icon: BarChart3, title: 'Transcripts & insights', body: 'Every call recorded, transcribed and summarized automatically.' },
  { icon: ShieldCheck, title: 'Private by design', body: 'Each account gets its own isolated, secure workspace.' },
]

const GoogleIcon = () => (
  <svg viewBox="0 0 48 48" className="h-5 w-5" aria-hidden>
    <path fill="#FFC107" d="M43.6 20.5h-1.9V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z"/>
    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
    <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.5-5.2l-6.2-5.3C29.2 35 26.7 36 24 36c-5.3 0-9.7-3.1-11.3-7.6l-6.5 5C9.5 39.6 16.2 44 24 44z"/>
    <path fill="#1976D2" d="M43.6 20.5H24v8h11.3c-.8 2.2-2.2 4-4 5.3l6.2 5.3C41.3 36 44 30.6 44 24c0-1.3-.1-2.3-.4-3.5z"/>
  </svg>
)

export default function LoginLanding() {
  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-slate-50">
      {/* Brand hero */}
      <div className="relative hidden lg:flex flex-col justify-between p-12 text-white overflow-hidden
        bg-gradient-to-br from-[#0d3a4f] via-[#134e68] to-[#0a2c3d]">
        {/* ambient accents */}
        <div className="absolute -top-24 -right-24 h-80 w-80 rounded-full bg-accent/20 blur-3xl" />
        <div className="absolute -bottom-32 -left-16 h-80 w-80 rounded-full bg-emerald-400/15 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <div className="h-11 w-11 rounded-2xl bg-white/10 backdrop-blur grid place-items-center text-2xl font-extrabold">L</div>
          <div>
            <div className="text-xl font-extrabold tracking-tight">LesCall</div>
            <div className="text-xs text-white/60">by Dhron AI</div>
          </div>
        </div>

        <div className="relative max-w-md">
          <div className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-emerald-300 mb-6">
            <Mic className="h-3.5 w-3.5" /> Real-time voice AI
          </div>
          <h1 className="text-4xl font-extrabold leading-tight">AI Voice Agents for Real Phone Calls</h1>
          <p className="mt-4 text-white/70 text-lg">
            Build, deploy and monitor voice agents that take and make real calls — with transcripts,
            summaries and analytics in one private workspace.
          </p>

          <div className="mt-10 grid sm:grid-cols-2 gap-5">
            {FEATURES.map((f) => (
              <div key={f.title} className="flex gap-3">
                <span className="h-9 w-9 shrink-0 rounded-xl bg-white/10 grid place-items-center text-emerald-300">
                  <f.icon className="h-4 w-4" />
                </span>
                <div>
                  <div className="font-semibold text-sm">{f.title}</div>
                  <div className="text-xs text-white/60 leading-relaxed">{f.body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative text-xs text-white/40">© {new Date().getFullYear()} Dhron AI · lescall.dhronai.com</div>
      </div>

      {/* Sign-in card */}
      <div className="flex items-center justify-center p-6 sm:p-12">
        <div className="w-full max-w-sm">
          {/* compact brand for mobile */}
          <div className="lg:hidden flex items-center gap-2.5 mb-8">
            <div className="h-10 w-10 rounded-2xl bg-brand grid place-items-center text-white text-xl font-extrabold">L</div>
            <div className="text-lg font-extrabold text-slate-900">LesCall</div>
          </div>

          <h2 className="text-2xl font-bold text-slate-900">Welcome back</h2>
          <p className="text-sm text-slate-500 mt-1.5 mb-8">Sign in to your LesCall workspace to manage your voice agents.</p>

          <a href="/api/auth/login"
            className="w-full inline-flex items-center justify-center gap-3 rounded-xl border border-slate-200
              bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm
              hover:bg-slate-50 hover:border-slate-300 transition">
            <GoogleIcon /> Continue with Google
          </a>

          <div className="mt-6 flex items-start gap-2 rounded-xl bg-emerald-50 border border-emerald-100 p-3">
            <ShieldCheck className="h-4 w-4 text-emerald-600 mt-0.5 shrink-0" />
            <p className="text-xs text-emerald-800">Every account gets its own private, isolated workspace — your agents and calls are never shared.</p>
          </div>

          <p className="mt-8 text-[11px] text-slate-400 text-center">
            By continuing you agree to LesCall's Terms of Service and Privacy Policy.
          </p>
        </div>
      </div>
    </div>
  )
}
