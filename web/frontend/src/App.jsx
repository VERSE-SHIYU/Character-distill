import useAppStore from './store/useAppStore'
import Sidebar from './components/Sidebar'
import TextPanel from './components/TextPanel'
import CharCard from './components/CharCard'
import ChatArea from './components/ChatArea'
import HistoryPanel from './components/HistoryPanel'
import SettingsPanel from './components/SettingsPanel'

const PANELS = {
  home: { icon: '📖', title: 'Character Simulator', sub: 'Upload text, distill characters, start chatting' },
  text: TextPanel,
  character: CharCard,
  chat: ChatArea,
  history: HistoryPanel,
  settings: SettingsPanel,
}

function MainContent() {
  const view = useAppStore((s) => s.currentView)
  const Panel = PANELS[view]

  if (typeof Panel === 'function') {
    return <Panel />
  }

  const info = Panel || PANELS.home
  return (
    <div className="shell-placeholder">
      <div className="shell-placeholder-inner">
        <div className="shell-placeholder-icon">{info.icon}</div>
        <div className="shell-placeholder-title">{info.title}</div>
        <div className="shell-placeholder-sub">{info.sub}</div>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-panel">
        <MainContent />
      </main>
    </div>
  )
}
