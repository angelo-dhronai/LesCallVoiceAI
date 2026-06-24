import {
  LayoutDashboard, Radio, ListChecks, Mic2, AlarmClock, ScrollText,
  MessagesSquare, BrainCircuit, Microscope, LineChart, Bot, PhoneOutgoing,
  Wallet, Settings2, HeartPulse, SlidersHorizontal,
} from 'lucide-react'

export type NavItem = { to: string; label: string; icon: any; badge?: boolean }
export type NavGroup = { title: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  {
    title: 'Monitoring',
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard },
      { to: '/live', label: 'Live Calls', icon: Radio, badge: true },
      { to: '/activity', label: 'Call Activity', icon: ListChecks },
      { to: '/recordings', label: 'Recordings', icon: Mic2 },
      { to: '/followups', label: 'Follow-Up Tracker', icon: AlarmClock },
      { to: '/logs', label: 'Live Logs', icon: ScrollText },
    ],
  },
  {
    title: 'Intelligence',
    items: [
      { to: '/transcripts', label: 'Call Transcripts', icon: MessagesSquare },
      { to: '/ai-analysis', label: 'AI Call Analysis', icon: BrainCircuit },
      { to: '/conv-analytics', label: 'Conversation Analytics', icon: Microscope },
      { to: '/analytics', label: 'Analytics', icon: LineChart },
      { to: '/followup-automation', label: 'Follow-Up Automation', icon: Bot },
    ],
  },
  {
    title: 'Campaigns',
    items: [{ to: '/outbound', label: 'Outbound Calls', icon: PhoneOutgoing }],
  },
  {
    title: 'Finance',
    items: [{ to: '/usage', label: 'API Usage', icon: Wallet }],
  },
  {
    title: 'System',
    items: [
      { to: '/control', label: 'Runtime Control', icon: SlidersHorizontal },
      { to: '/health', label: 'Health', icon: HeartPulse },
      { to: '/agent-settings', label: 'Agent Settings', icon: Settings2 },
    ],
  },
]
