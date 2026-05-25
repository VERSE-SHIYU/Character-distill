import { useEffect, useState, useCallback, useRef } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'
import { fetchWithTimeout } from '../api/client'

const NAV_ITEMS = [
  { id: 'home',      icon: '\u{1F3E0}', label: '首页' },
  { id: 'feed',      icon: '\u{1F4AA}', label: '动态' },
  { id: 'workbench', icon: '\u{1F4DD}', label: '工作台' },
  { id: 'market',    icon: '\u{1F30D}', label: '市场' },
  { id: 'history',   icon: '\u{1F4CB}', label: '记录' },
  { id: 'trash',     icon: '\u{1F5D1}', label: '回收站' },
  { id: 'mine',      icon: '\u{1F464}', label: '我的' },
]

export default function Sidebar({ open, pinned, onShow, onHide, onTogglePin }) {
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const startChat = useAppStore((s) => s.startChat)
  const authUser = useAppStore((s) => s.authUser)
  const logout = useAppStore((s) => s.logout)
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)
  const [unreadCount, setUnreadCount] = useState(0)
  const [showTheme, setShowTheme] = useState(false)

  const isVisible = open || pinned

  // Poll unread message count
  useEffect(() => {
    const poll = () => {
      fetchWithTimeout('/api/messages/unread-count')
        .then((r) => r.json())
        .then((d) => setUnreadCount(d.count ?? 0))
        .catch(() => {})
    }
    poll()
    const timer = setInterval(poll, 30000)
    return () => clearInterval(timer)
  }, [])

  function isActive(id) {
    switch (id) {
      case 'feed': return currentView === 'feed'
      case 'workbench': return ['text', 'character', 'chat'].includes(currentView)
      case 'market': return ['market', 'author', 'textDetail'].includes(currentView)
      case 'history': return currentView === 'history'
      case 'mine': return ['mine', 'messages', 'admin'].includes(currentView)
      default: return currentView === id
    }
  }

  const handleNav = useCallback((id) => {
    switch (id) {
      case 'workbench': setView('text'); break
      case 'trash': setView('trash'); break
      case 'mine': setView('mine'); break
      default: setView(id)
    }
  }, [setView])

  const navItems = authUser?.is_admin
    ? [...NAV_ITEMS, { id: 'admin', icon: '\u{1F6E1}', label: '管理' }]
    : NAV_ITEMS

  let sidebarClass = 'sidebar'
  if (open && !pinned) sidebarClass += ' open'
  if (pinned) sidebarClass += ' pinned'

  return (
    <aside
      className={sidebarClass}
      onMouseEnter={onShow}
      onMouseLeave={onHide}
    >
      <SidebarHeader pinned={pinned} onTogglePin={onTogglePin} />

      {isVisible && (
        <nav className="sidebar-nav">
          {navItems.map(({ id, icon, label }) => (
            <button
              key={id}
              type="button"
              className={`sidebar-item${isActive(id) ? ' active' : ''}`}
              onClick={() => handleNav(id)}
            >
              <span className="sidebar-item-icon">{icon}</span>
              <span className="sidebar-item-label">{label}</span>
              {id === 'history' && unreadCount > 0 && (
                <span className="sidebar-item-badge">{unreadCount}</span>
              )}
            </button>
          ))}
          {isActive('workbench') && currentView !== 'chat' && currentCard && sessionId && (
            <button type="button" className="sidebar-item sidebar-chat-resume" onClick={() => setView('chat')}>
              <span className="sidebar-item-icon">{'\u{1F4AC}'}</span>
              <span className="sidebar-item-label">继续对话</span>
            </button>
          )}
        </nav>
      )}

      {isVisible && authUser && (
        <div className="sidebar-user-info">
          <button
            type="button"
            className="sidebar-user-link"
            onClick={() => setView('profile')}
            title="个人设置"
          >
            <Avatar name={authUser.username} src={useAppStore.getState().userAvatar} size={38} />
            <span className="sidebar-user-name">{authUser.username}</span>
          </button>
          <div className="sidebar-user-actions">
            <button
              type="button"
              className="sidebar-settings-btn"
              onClick={() => setView('voice')}
              title="音色管理"
            >
              {'\u{1F399}'}
            </button>
            <div className="sidebar-theme-wrap">
              <button
                type="button"
                className="sidebar-theme-btn"
                onClick={() => setShowTheme(v => !v)}
                title="切换主题"
              >
                🎨
              </button>
              {showTheme && <ThemePopup onClose={() => setShowTheme(false)} />}
            </div>
            <button
              type="button"
              className="sidebar-settings-btn"
              onClick={() => setView('settings')}
              title="设置"
            >
              ⚙️
            </button>
            <button className="sidebar-logout-btn" onClick={logout} title="退出登录">
              退出
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}

const THEMES = [
  { key: 'milktea',  emoji: '\u{1F375}', label: '抹茶' },
  { key: 'ocean',    emoji: '\u{1F30A}', label: '海盐' },
  { key: 'sakura',   emoji: '\u{1F338}', label: '樱花' },
  { key: 'midnight', emoji: '\u{1F311}', label: '午夜' },
]

function ThemePopup({ onClose }) {
  const [current, setCurrent] = useState(() => {
    try { return localStorage.getItem('charsim-theme') || 'milktea' } catch { return 'milktea' }
  })
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current?.closest('.sidebar-theme-wrap')?.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const apply = (key) => {
    document.documentElement.className = `theme-${key}`
    try { localStorage.setItem('charsim-theme', key) } catch {}
    setCurrent(key)
  }

  return (
    <div className="theme-popup" ref={ref}>
      {THEMES.map(t => (
        <button key={t.key} type="button"
          className={`theme-popup-item${current === t.key ? ' active' : ''}`}
          onClick={() => apply(t.key)}>
          <span className="theme-popup-emoji">{t.emoji}</span>
          <span className="theme-popup-label">{t.label}</span>
          {current === t.key && <span className="theme-popup-check">✓</span>}
        </button>
      ))}
    </div>
  )
}

function SidebarHeader({ pinned, onTogglePin }) {
  return (
    <div className="sidebar-header">
      <div className="sidebar-logo-wrap">
        <div className="sidebar-logo">
          <span>{'\u{1F4D6}'}</span>
          <span className="sidebar-logo-text">CharSim</span>
        </div>
        <div className="sidebar-sub">
          {'角色蒸馏与沉浸式对话'}
        </div>
      </div>
      <button
        type="button"
        className="sidebar-collapse-btn"
        onClick={onTogglePin}
        aria-label={pinned ? '收起侧边栏' : '固定侧边栏'}
        title={pinned ? '取消固定' : '固定'}
      >
        {pinned ? '✕' : '📌'}
      </button>
    </div>
  )
}
