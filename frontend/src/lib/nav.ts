import {
  Radio, ListChecks, Mic2,
  MessagesSquare, BrainCircuit, Microscope, PhoneOutgoing,
  Boxes, CalendarClock, FileText, LayoutDashboard,
  MessageCircle, PhoneForwarded, Settings as SettingsIcon, BookUser,
} from 'lucide-react'

// Every user sees the same navigation — roles are flattened; there is no
// admin tier and no admin-only pages.
//
// Group order follows the tenant journey: set up (Build → Numbers →
// Campaigns), then operate (Monitoring), then learn (Intelligence).
export type NavItem = { to: string; label: string; icon: any; badge?: boolean }
export type NavGroup = { title: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  {
    title: 'Home',
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard },
      { to: '/copilot', label: 'Copilot', icon: MessageCircle },
    ],
  },
  {
    // One panel for everything agent-related: the list, the "New Agent"
    // button, and the intent tester (a tab) all live under /build/agents.
    title: 'Build',
    items: [
      { to: '/build/agents', label: 'Agents', icon: Boxes },
      { to: '/telephony', label: 'Numbers (BYON)', icon: PhoneForwarded },
    ],
  },
  {
    title: 'Campaigns',
    items: [
      { to: '/contacts', label: 'Contacts', icon: BookUser },
      { to: '/outbound', label: 'Outbound Calls', icon: PhoneOutgoing },
    ],
  },
  {
    title: 'Monitoring',
    items: [
      { to: '/live', label: 'Live Calls', icon: Radio, badge: true },
      { to: '/activity', label: 'Call Activity', icon: ListChecks },
      { to: '/recordings', label: 'Recordings', icon: Mic2 },
      { to: '/transcripts', label: 'Call Transcripts', icon: MessagesSquare },
    ],
  },
  {
    // Post-call intelligence is automatic (deploy/PLAN-intelligence-layer.md):
    // every completed call is analyzed by the pipeline — there is no manual
    // "analyze" step anymore.
    title: 'Intelligence',
    items: [
      { to: '/intelligence', label: 'Intelligence', icon: BrainCircuit },
      { to: '/followups', label: 'Follow-ups', icon: CalendarClock },
      { to: '/reports', label: 'Reports', icon: FileText },
      { to: '/conv-analytics', label: 'Conversation Analytics', icon: Microscope },
    ],
  },
  {
    title: 'Account',
    items: [{ to: '/settings', label: 'Settings', icon: SettingsIcon }],
  },
]
