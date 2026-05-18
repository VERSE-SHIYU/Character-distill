import { useEffect, useRef, useState, useCallback } from 'react'
import useAppStore from './store/useAppStore'
import { initTheme } from './utils/theme'
import { fetchWithTimeout } from './api/client'
import Sidebar from './components/Sidebar'
import TextPanel from './components/TextPanel'
import CharCard from './components/CharCard'
import ChatArea from './components/ChatArea'
import HistoryPanel from './components/HistoryPanel'
import SettingsPanel from './components/SettingsPanel'
import HomePage from './components/HomePage'
import VoicePanel from './components/VoicePanel'

const PANELS = {
  home: HomePage,
  text: TextPanel,
  character: CharCard,
  chat: ChatArea,
  history: HistoryPanel,
  settings: SettingsPanel,
  voice: VoicePanel,
}

function MainContent() {
  const view = useAppStore((s) => s.currentView)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    setVisible(false)
    const t = requestAnimationFrame(() => setVisible(true))
    return () => cancelAnimationFrame(t)
  }, [view])

  const Panel = PANELS[view] || HomePage
  return (
    <div className={`view-transition${visible ? ' in' : ''}`}>
      <Panel />
    </div>
  )
}

export default function App() {
  const checkVoiceStatus = useAppStore((s) => s.checkVoiceStatus)
  const setView = useAppStore((s) => s.setView)
  const currentView = useAppStore((s) => s.currentView)

  useEffect(() => {
    initTheme()
  }, [])

  useEffect(() => {
    checkVoiceStatus()
  }, [checkVoiceStatus])

  // Auto-redirect to settings if LLM is not configured (check once on mount)
  useEffect(() => {
    ;(async () => {
      const configured = await useAppStore.getState().checkApiConfig()
      if (!configured) {
        setView('settings')
      }
    })()
  }, [])

  // Sidebar auto-hide state
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarPinned, setSidebarPinned] = useState(false)
  const leaveTimer = useRef(null)
  const isMobile = useRef(window.innerWidth <= 768)

  useEffect(() => {
    const onResize = () => { isMobile.current = window.innerWidth <= 768 }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const isSidebarVisible = sidebarOpen || sidebarPinned

  const showSidebar = useCallback(() => {
    if (leaveTimer.current) { clearTimeout(leaveTimer.current); leaveTimer.current = null }
    setSidebarOpen(true)
    document.body.style.cursor = 'default'
  }, [])

  const hideSidebar = useCallback(() => {
    if (sidebarPinned) return
    leaveTimer.current = setTimeout(() => setSidebarOpen(false), 300)
  }, [sidebarPinned])

  const togglePin = useCallback(() => {
    if (sidebarPinned) {
      setSidebarPinned(false)
      setSidebarOpen(false)
    } else {
      setSidebarPinned(true)
      setSidebarOpen(true)
    }
  }, [sidebarPinned])

  const closeMobile = useCallback(() => {
    setSidebarOpen(false)
    setSidebarPinned(false)
  }, [])

  return (
    <div className={`app-shell${isSidebarVisible ? ' has-sidebar-open' : ''}`}>
      {/* Trigger zone */}
      {!isSidebarVisible && (
        <div
          className="sidebar-trigger"
          onMouseEnter={showSidebar}
        />
      )}

      <Sidebar
        open={sidebarOpen}
        pinned={sidebarPinned}
        onShow={showSidebar}
        onHide={hideSidebar}
        onTogglePin={togglePin}
      />

      {/* Mobile overlay */}
      <div
        className={`sidebar-overlay${isSidebarVisible ? ' active' : ''}`}
        onClick={closeMobile}
      />

      <main className="main-panel">
        <MainContent />
      </main>
    </div>
  )
}
