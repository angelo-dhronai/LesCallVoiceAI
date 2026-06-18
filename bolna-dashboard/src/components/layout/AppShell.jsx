import { useState } from 'react'
import Sidebar from './Sidebar.jsx'
import TopHeader from './TopHeader.jsx'

function AppShell({ current, onNavigate, breadcrumb, dark, onToggleDark, children }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="flex min-h-screen bg-gray-50 dark:bg-[#0b0d12]">
      <Sidebar
        current={current}
        onNavigate={onNavigate}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((v) => !v)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopHeader breadcrumb={breadcrumb} dark={dark} onToggleDark={onToggleDark} />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  )
}

export default AppShell
