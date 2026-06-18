# Bolna.ai Dashboard (UI build)

Production-grade React + Tailwind frontend for an AI Voice Agent platform.
Mock-data driven (all data in `src/data/mockData.js`) — standalone from the live
lescall backend.

## Run
```bash
cd bolna-dashboard
npm install
npm run dev      # http://localhost:5174
npm run build    # production build -> dist/
```

## Stack
React 18 (hooks only) · Tailwind (dark mode via `class`) · lucide-react · recharts.
State is React-only (no Redux/router/localStorage).

## Structure
- `src/components/layout` — Sidebar, TopHeader, AppShell
- `src/components/agent`  — Agent config (list, tabs, cost bar, prompt canvas)
- `src/components/calls`  — Call Detail cockpit (analytics, waveform player, transcript, notes)
- `src/components/flow`   — LLM flow node graph + inspector flyout
- `src/components/tables` — Call history table, knowledge base grid
- `src/data/mockData.js`  — single source of truth for all data
- `src/App.jsx`           — state-based view router
