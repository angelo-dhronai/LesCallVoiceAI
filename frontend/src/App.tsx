import { Routes, Route } from 'react-router-dom'
import { AppShell } from './layout/AppShell'
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
import AgentSettings from './pages/AgentSettings'

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Overview />} />
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
        <Route path="/agent-settings" element={<AgentSettings />} />
        <Route path="*" element={<Placeholder title="Not Found" sub="That route does not exist." />} />
      </Routes>
    </AppShell>
  )
}
