import { useEffect, useRef, useState, useCallback } from 'react'
import useAppStore from './store/useAppStore'
import { initTheme } from './utils/theme'
import { fetchWithTimeout, getToken } from './api/client'
import Sidebar from './components/Sidebar'
import TextPanel from './components/TextPanel'
import CharCard from './components/CharCard'
import ChatArea from './components/ChatArea'
import HistoryPanel from './components/HistoryPanel'
import SettingsPanel from './components/SettingsPanel'
import HomePage from './components/HomePage'
import VoicePanel from './components/VoicePanel'
import LoginPage from './components/LoginPage'
import AdminPanel from './components/AdminPanel'
import ProfilePage from './components/ProfilePage'
import MarketPage from './components/MarketPage'
import GroupChatPage from './components/GroupChatPage'
import DistillTaskBar from './components/DistillTaskBar'

const PANELS = {
  home: HomePage,
  text: TextPanel,
  character: CharCard,
  chat: ChatArea,
  history: HistoryPanel,
  settings: SettingsPanel,
  voice: VoicePanel,
  login: LoginPage,
  admin: AdminPanel,
  profile: ProfilePage,
  market: MarketPage,
  groupChat: GroupChatPage,
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
  const isLoggedIn = useAppStore((s) => s.isLoggedIn)
  const logout = useAppStore((s) => s.logout)
  const [authChecking, setAuthChecking] = useState(true)

  useEffect(() => {
    initTheme()
  }, [])

  useEffect(() => {
    checkVoiceStatus()
  }, [checkVoiceStatus])

  // Verify token on mount + check API config (single /api/auth/me call)
  useEffect(() => {
    ;(async () => {
      const token = getToken()
      if (!token) {
        setView('login')
        setAuthChecking(false)
        return
      }
      try {
        const res = await fetchWithTimeout('/api/auth/me')
        const user = await res.json()
        useAppStore.setState({ authUser: user, isLoggedIn: true })
        if (user.avatar_data) {
          useAppStore.setState({ userAvatar: user.avatar_data })
        }
        useAppStore.getState().restoreDistillTasks()

        const configured = !!user.has_api_key
        useAppStore.setState({ apiConfigured: configured })
        if (!configured) {
          setView('settings')
        }
      } catch {
        logout()
        setView('login')
      } finally {
        setAuthChecking(false)
      }
    })()
  }, [])

  // Redirect to login if logged out
  useEffect(() => {
    if (!isLoggedIn) {
      setView('login')
    }
  }, [isLoggedIn])

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

  if (authChecking) {
    return <div className="admin-loading">验证登录状态…</div>
  }

  if (currentView === 'login') {
    return <LoginPage />
  }

  return (
    <div className={`app-shell${isSidebarVisible ? ' has-sidebar-open' : ''}`}>
      {/* Trigger zone + visible toggle when collapsed */}
      {!isSidebarVisible && (
        <div className="sidebar-trigger" onMouseEnter={showSidebar}>
          <button
            type="button"
            className="sidebar-toggle-btn"
            onClick={togglePin}
            title="展开侧边栏"
            aria-label="展开侧边栏"
          >
            ▶
          </button>
        </div>
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
      <DistillTaskBar />
    </div>
  )
}
