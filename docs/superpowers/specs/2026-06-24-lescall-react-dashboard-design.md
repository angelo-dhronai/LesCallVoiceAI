# LesCall — Premium React Dashboard (Design Spec)

**Date:** 2026-06-24
**Status:** Approved for planning
**Author:** Claude + tech@dhronai.com

## Goal

Replace the existing 17-page vanilla-JS dashboard (`dashboard/static/`) with a
premium React rewrite that keeps **full functional parity** while elevating the
visual design to an elite, enterprise-grade SaaS look. Containerize the
**frontend only** (the FastAPI backend on `:8080` stays as-is).

## Decisions (locked)

| Decision | Choice |
| --- | --- |
| Scope | Full parity — all 17 pages |
| Theme | Off-white light primary + **mint `#00F5A0`** + **indigo `#4F46E5`** accents, plus dark "obsidian" mode |
| Backend wiring | Container nginx proxies `/api` → `host.docker.internal:8080` (configurable `BACKEND_URL`); browser talks only to the frontend origin |
| Containerization | Frontend only — multi-stage Docker (`node:20-alpine` build → `nginx:alpine` serve) |

## Stack

- **Vite + React 18 + TypeScript**
- **Tailwind CSS** + custom token layer (mint/indigo, off-white surfaces, glass utilities, 12–16px radius)
- **React Router** — one route per page
- **TanStack Query** — fetch/cache/poll `/api/*`
- Native **EventSource** for SSE streams
- **Recharts** for donut/bar charts; custom SVG `Sparkline` / `RadialRing` for in-card micro-viz
- **lucide-react** icons
- **Inter** (UI) + **Plus Jakarta Sans / Geist Mono** (metric values) via fontsource

## Project layout

New `frontend/` at repo root. Existing `dashboard/` (backend + old static) untouched.

```
frontend/
  src/
    api/        client.ts (typed fetch), hooks/ (React Query per domain), sse.ts
    theme/      tokens.ts, ThemeProvider.tsx, glass.css
    components/ Card, BentoGrid, StatCard, Sparkline, RadialRing, Donut,
                BarChart, DataTable, Badge, Button, Input, Select, Modal,
                SlideOver, Sidebar, Topbar, HealthPill, EmptyState, Spinner
    layout/     AppShell.tsx
    pages/      Overview, LiveCalls, CallActivity, Recordings, Followups,
                Logs, Transcripts, AiAnalysis, ConvAnalytics, Analytics,
                FollowupAutomation, Outbound, ApiUsage, RuntimeControl,
                Health, AgentSettings  (16 routes; Overview is index)
    lib/        format.ts (₹, duration, dates), constants.ts
    App.tsx, main.tsx
  Dockerfile, nginx.conf.template, docker-compose.yml, .dockerignore,
  vite.config.ts, tailwind.config.ts, tsconfig.json, package.json
```

## Pages & their endpoints (parity map)

| Page | Key endpoints | Notes |
| --- | --- | --- |
| Overview | `/api/overview`, `/api/minutes/quota`, `/api/health` | Bento grid + KPI period tabs (today/week/month) |
| Live Calls | `/api/calls/active`, SSE `/api/calls/live-state/stream` | Live grid, view-conversation slide-over |
| Call Activity | `/api/calls/history` | Filter/sort/paginate table |
| Recordings | `/api/calls/recordings`, `…/audio` | Audio player + download, search |
| Follow-Up Tracker | `/api/followups`, `/api/followups/manual` | Status filter, add/complete, automate action |
| Live Logs | SSE `/api/logs/stream`, `/api/logs/tail` | Category filters, autoscroll |
| Call Transcripts | `/api/calls/recordings`, `…/transcript`, `…/transcripts/search` | Timeline view + inline intelligence pills |
| AI Call Analysis | `/api/analysis/eligible`, `/api/analysis/run`, `…/analysis` | Select + analyze on demand |
| Conversation Analytics | `/api/analytics/conversation` | Semi-donut sentiment, objection cluster |
| Analytics | `/api/overview`, `/api/outbound/stats` | Charts derived from existing data |
| Follow-Up Automation | `/api/followup-automation`, `…/call-now` | Schedule table, call-now |
| Outbound | `/api/outbound/*`, `/api/outbound/campaign/*` | Import, campaign runner, contact table |
| API Usage | `/api/sarvam/*` | Credits, key pool mgmt, usage breakdown |
| Runtime Control | `/api/runtime/*`, SSE `/api/runtime/output/stream` | Start/stop/restart, terminal output |
| Health | `/api/health` | Service/port status |
| Agent Settings | `/api/agent/config` | Read-only config view |

## Design system (per brief)

- **Surfaces:** off-white base; glass cards = subtle gradient + layered shadow
  (`0 4px 30px rgba(0,0,0,.04)`) + `backdrop-blur`. Full dark obsidian mode.
- **Sidebar:** floating, collapsible, glass, high-contrast active indicator.
- **Topbar:** the 6 status pills collapse into one **System Health** dropdown.
- **Overview:** bento grid — large hero billing card; each metric card embeds a
  sparkline or radial ring, never a bare number.
- **Transcripts:** alternating timeline (no chat bubbles) + inline intelligence
  pills (e.g. `Intent: Immediate Buy`).
- **Conversation Analytics:** semi-donut sentiment (emerald/crimson/amber) +
  expandable objection cluster.
- **Tables:** borderless, zebra striping, hover-revealed actions, ample padding.
- **Type/shape:** Inter UI; Plus Jakarta Sans / Geist Mono metrics; 12–16px radius.

## Data wiring

- **Dev:** Vite proxy `/api` → `http://localhost:8080` (SSE included).
- **Prod:** nginx serves the static build, SPA fallback `try_files … /index.html`,
  and proxies `/api` → `${BACKEND_URL}` (default `host.docker.internal:8080`)
  with SSE-safe settings (`proxy_buffering off`, `proxy_read_timeout 3600s`).
  `BACKEND_URL` injected at container start via `envsubst` on `nginx.conf.template`.

## Container

- Multi-stage: build with `node:20-alpine` (`npm ci && npm run build`), serve the
  `dist/` with `nginx:alpine`.
- Entrypoint runs `envsubst` to render nginx config from `BACKEND_URL`, then
  `nginx -g 'daemon off;'`.
- `docker-compose.yml` maps `host.docker.internal:host-gateway` and exposes `:80`.

## Error handling & states

- Every data view has loading (skeleton/spinner), empty, and error states.
- SSE auto-reconnect with backoff; show connection status dot.
- React Query retries + stale-while-revalidate; poll intervals match current JS
  (live data ~ a few seconds, dashboards ~ 30s).

## Out of scope

- Backend changes (no new endpoints; consume existing API verbatim).
- Auth (the current dashboard has none).
- Containerizing the backend.

## Verification

- `npm run build` clean; `npm run dev` renders all 17 routes against a running
  backend.
- Docker image builds; `docker compose up` serves the app and proxies `/api`.
- Spot-check parity: each page loads its data, key actions work (start campaign,
  run analysis, play recording, runtime start/stop, theme toggle, dark mode).
