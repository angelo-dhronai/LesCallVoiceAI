import {
  Radio, ListChecks, Mic2,
  MessagesSquare, BrainCircuit, Microscope, PhoneOutgoing,
  Boxes,
  MessageCircle, PhoneForwarded, Settings as SettingsIcon, BookUser,
} from 'lucide-react'

// Every user sees the same navigation — roles are flattened; there is no
// admin tier and no admin-only pages.
export type NavItem = { to: string; label: string; icon: any; badge?: boolean }
export type NavGroup = { title: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  {
    title: 'Copilot',
    items: [{ to: '/', label: 'Copilot', icon: MessageCircle }],
  },
  {
    title: 'Monitoring',
    items: [
      { to: '/live', label: 'Live Calls', icon: Radio, badge: true },
      { to: '/activity', label: 'Call Activity', icon: ListChecks },
      { to: '/recordings', label: 'Recordings', icon: Mic2 },
    ],
  },
  {
    title: 'Intelligence',
    items: [
      { to: '/transcripts', label: 'Call Transcripts', icon: MessagesSquare },
      { to: '/ai-analysis', label: 'AI Call Analysis', icon: BrainCircuit },
      { to: '/conv-analytics', label: 'Conversation Analytics', icon: Microscope },
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
]
