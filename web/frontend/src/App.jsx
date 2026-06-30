import { lazy, Suspense, useEffect, useRef, useState, useCallback } from 'react'
import useAppStore from './store/useAppStore'
import { initTheme } from './utils/theme'
import { fetchWithTimeout, getToken } from './api/client'
import Sidebar from './components/Sidebar'
import LoginPage from './components/LoginPage'
import DistillTaskBar from './components/DistillTaskBar'
import ArchiveListModal from './components/ArchiveListModal'
import CrossBorderConsentModal from './components/CrossBorderConsentModal'
import Loading from './components/common/Loading'

const TextPanel = lazy(() => import('./components/TextPanel'))
const CharCard = lazy(() => import('./components/CharCard'))
const ChatArea = lazy(() => import('./components/ChatArea'))
const HistoryPanel = lazy(() => import('./components/HistoryPanel'))
const SettingsPanel = lazy(() => import('./components/SettingsPanel'))
const HomePage = lazy(() => import('./components/HomePage'))
const VoicePanel = lazy(() => import('./components/VoicePanel'))
const AdminPanel = lazy(() => import('./components/AdminPanel'))
const ProfilePage = lazy(() => import('./components/ProfilePage'))
const MarketPage = lazy(() => import('./components/MarketPage'))
const AuthorPage = lazy(() => import('./components/AuthorPage'))
const GroupChatPage = lazy(() => import('./components/GroupChatPage'))
const TextDetailPage = lazy(() => import('./components/TextDetailPage'))
const MessagesPage = lazy(() => import('./components/MessagesPage'))
const MinePage = lazy(() => import('./components/MinePage'))
const TrashPage = lazy(() => import('./components/TrashPage'))
const MarketCardDetail = lazy(() => import('./components/MarketCardDetail'))
const FeedPage = lazy(() => import('./components/FeedPage'))
const BookReader = lazy(() => import('./components/BookReader'))
const LegalPage = lazy(() => import('./components/LegalPage'))

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
  marketCardDetail: MarketCardDetail,
  feed: FeedPage,
  author: MinePage,
  groupChat: GroupChatPage,
  textDetail: TextDetailPage,
  messages: MessagesPage,
  mine: MinePage,
  reader: BookReader,
  trash: TrashPage,
  legal: LegalPage,
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
      <Suspense fallback={<Loading text="加载中…" />}>
        <Panel />
      </Suspense>
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
  const [announcement, setAnnouncement] = useState(null)
  const [annDismissed, setAnnDismissed] = useState(false)

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

        // Restore saved navigation state (skip login/settings — login is for unauthenticated, settings is handled by apiConfigured check)
        const savedView = localStorage.getItem('nav_view') || 'home'
        const restoreView = (savedView === 'login' || savedView === 'settings') ? 'home' : savedView
        const navUpdates = { authUser: user, isLoggedIn: true, currentView: restoreView }
        if (savedView === 'author' || savedView === 'mine') {
          const savedId = localStorage.getItem('nav_author_user_id')
          if (savedId) navUpdates.authorUserId = savedId
        }
        if (savedView === 'marketCardDetail') {
          const savedId = localStorage.getItem('nav_market_card_id')
          if (savedId) navUpdates.currentMarketCardId = savedId
        }
        if (savedView === 'textDetail') {
          const savedId = localStorage.getItem('nav_text_detail_id')
          if (savedId) navUpdates.currentTextDetailId = savedId
        }
        if (savedView === 'messages') {
          const savedId = localStorage.getItem('nav_msg_target_user_id')
          if (savedId) navUpdates.messageTargetUserId = savedId
        }
        useAppStore.setState(navUpdates)
        if (user.avatar_data) {
          useAppStore.setState({ userAvatar: user.avatar_data })
        }
        useAppStore.getState().restoreDistillTasks()

        const configured = !!user.has_api_key
        useAppStore.setState({ apiConfigured: configured })
        if (!configured) {
          setView('settings')
        }
        // Fetch active announcement
        try {
          const annRes = await fetchWithTimeout('/api/announcement/active')
          const annData = await annRes.json()
          if (annData && annData.content) setAnnouncement(annData)
        } catch {}
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

  // Global handler: refresh token expired → log out
  useEffect(() => {
    const handler = () => {
      logout()
      setView('login')
    }
    window.addEventListener('auth:expired', handler)
    return () => window.removeEventListener('auth:expired', handler)
  }, [logout])

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

  if (currentView === 'login' || (currentView === 'legal' && !isLoggedIn)) {
    if (currentView === 'legal') return (<Suspense fallback={<Loading text="加载中…" />}><LegalPage /></Suspense>)
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
        {announcement && !annDismissed && (
          <div className="announcement-banner">
            <span className="announcement-banner-text" style={{ whiteSpace: 'pre-wrap', textAlign: announcement.align || 'left' }}>{announcement.content}</span>
            <button className="announcement-banner-close" onClick={() => setAnnDismissed(true)}>✕</button>
          </div>
        )}
        <MainContent />
      </main>
      <DistillTaskBar />
      <ArchiveListModal />
      <CrossBorderConsentModal />
    </div>
  )
}
