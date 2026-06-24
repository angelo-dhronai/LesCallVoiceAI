import { Routes, Route } from 'react-router-dom'
import { AppShell } from './layout/AppShell'
import Overview from './pages/Overview'
import Placeholder from './pages/Placeholder'
import { NAV } from './lib/nav'

const PAGES: Record<string, { title: string; sub?: string }> = Object.fromEntries(
  NAV.flatMap((g) => g.items).map((i) => [i.to, { title: i.label }]),
)

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Overview />} />
        {NAV.flatMap((g) => g.items)
          .filter((i) => i.to !== '/')
          .map((i) => (
            <Route key={i.to} path={i.to} element={<Placeholder title={PAGES[i.to].title} />} />
          ))}
        <Route path="*" element={<Placeholder title="Not Found" sub="That route does not exist yet." />} />
      </Routes>
    </AppShell>
  )
}
