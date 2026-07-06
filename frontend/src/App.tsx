import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './layout/AppShell'
import CopilotChat from './pages/CopilotChat'
import Overview from './pages/Overview'
import Placeholder from './pages/Placeholder'
import Live from './pages/Live'
import Activity from './pages/Activity'
import Recordings from './pages/Recordings'
import Followups from './pages/Followups'
import Logs from './pages/Logs'
import Transcripts from './pages/Transcripts'
import AiAnalysis from './pages/AiAnalysis'
import ConvAnalytics from './pages/ConvAnalytics'
import Analytics from './pages/Analytics'
import FollowupAutomation from './pages/FollowupAutomation'
import Outbound from './pages/Outbound'
import Usage from './pages/Usage'
import Control from './pages/Control'
import Health from './pages/Health'
import AgentsList from './pages/agents/AgentsList'
import CreateAgent from './pages/agents/CreateAgent'
import EditAgent from './pages/agents/EditAgent'
import IntentPreview from './pages/agents/IntentPreview'

export default function App() {
  return (
    <AppShell>
      <Routes>
        {/* Homepage: conversational Agent Copilot */}
        <Route path="/" element={<CopilotChat />} />
        <Route path="/overview" element={<Overview />} />
        <Route path="/live" element={<Live />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/recordings" element={<Recordings />} />
        <Route path="/followups" element={<Followups />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/transcripts" element={<Transcripts />} />
        <Route path="/ai-analysis" element={<AiAnalysis />} />
        <Route path="/conv-analytics" element={<ConvAnalytics />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/followup-automation" element={<FollowupAutomation />} />
        <Route path="/outbound" element={<Outbound />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/control" element={<Control />} />
        <Route path="/health" element={<Health />} />
        {/* Build — Agent Builder grafted onto the main dashboard */}
        <Route path="/build/agents" element={<AgentsList />} />
        <Route path="/build/agents/new" element={<CreateAgent />} />
        <Route path="/build/agents/:name" element={<EditAgent />} />
        <Route path="/build/intent" element={<IntentPreview />} />
        {/* Old read-only Agent Settings is superseded by the editable builder */}
        <Route path="/agent-settings" element={<Navigate to="/build/agents" replace />} />
        <Route path="*" element={<Placeholder title="Not Found" sub="That route does not exist." />} />
      </Routes>
    </AppShell>
  )
}
