import {
  LayoutDashboard, Radio, ListChecks, Mic2, AlarmClock, ScrollText,
  MessagesSquare, BrainCircuit, Microscope, LineChart, Bot, PhoneOutgoing,
  Wallet, HeartPulse, SlidersHorizontal, Boxes,
  MessageCircle, PhoneForwarded, Settings as SettingsIcon, BookUser,
} from 'lucide-react'

// admin: only platform admins see this item/group. Per-item `admin` overrides
// the group's default so a mostly-admin group can still expose owner-scoped
// pages (e.g. Recordings) to tenants.
export type NavItem = { to: string; label: string; icon: any; badge?: boolean; admin?: boolean }
export type NavGroup = { title: string; items: NavItem[]; admin?: boolean }

export const NAV: NavGroup[] = [
  {
    title: 'Copilot',
    items: [{ to: '/', label: 'Copilot', icon: MessageCircle }],
  },
  {
    // Call Activity / Recordings are owner-scoped (tenant-visible);
    // Overview / Live Calls / Follow-Up Tracker / Logs are admin monitoring.
    title: 'Monitoring',
    items: [
      { to: '/overview', label: 'Overview', icon: LayoutDashboard, admin: true },
      { to: '/live', label: 'Live Calls', icon: Radio, badge: true },
      { to: '/activity', label: 'Call Activity', icon: ListChecks },
      { to: '/recordings', label: 'Recordings', icon: Mic2 },
      { to: '/followups', label: 'Follow-Up Tracker', icon: AlarmClock, admin: true },
      { to: '/logs', label: 'Live Logs', icon: ScrollText, admin: true },
    ],
  },
  {
    // Transcripts / AI Analysis / Conversation Analytics are owner-scoped;
    // Analytics(summary) + Follow-up Automation remain admin.
    title: 'Intelligence',
    items: [
      { to: '/transcripts', label: 'Call Transcripts', icon: MessagesSquare },
      { to: '/ai-analysis', label: 'AI Call Analysis', icon: BrainCircuit },
      { to: '/conv-analytics', label: 'Conversation Analytics', icon: Microscope },
      { to: '/analytics', label: 'Analytics', icon: LineChart, admin: true },
      { to: '/followup-automation', label: 'Follow-Up Automation', icon: Bot, admin: true },
    ],
  },
  {
    // One panel for everything agent-related: the list, the "New Agent"
    // button, and the intent tester (a tab) all live under /build/agents.
    title: 'Build',
    items: [
      { to: '/build/agents', label: 'Agents', icon: Boxes },
    ],
  },
  {
    title: 'Telephony',
    items: [{ to: '/telephony', label: 'Numbers (BYON)', icon: PhoneForwarded }],
  },
  {
    title: 'Campaigns',
    items: [
      { to: '/contacts', label: 'Contacts', icon: BookUser },
      { to: '/outbound', label: 'Outbound Calls', icon: PhoneOutgoing },
    ],
  },
  {
    title: 'Account',
    items: [{ to: '/settings', label: 'Settings', icon: SettingsIcon }],
  },
  {
    title: 'Finance',
    admin: true,
    items: [{ to: '/usage', label: 'API Usage', icon: Wallet }],
  },
  {
    title: 'System',
    admin: true,
    items: [
      { to: '/control', label: 'Runtime Control', icon: SlidersHorizontal },
      { to: '/health', label: 'Health', icon: HeartPulse },
    ],
  },
]
