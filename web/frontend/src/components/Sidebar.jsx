import { useEffect, useState, useCallback, useRef } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'
import { fetchWithTimeout } from '../api/client'

function Svg({ d, viewBox = '0 0 24 24', children, size = 20 }) {
  return (
    <svg viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={size} height={size}>
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const NAV_ITEMS = [
  {
    id: 'home',
    icon: <Svg size={20} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"><polyline points="9 22 9 12 15 12 15 22" /></Svg>,
    label: '首页',
  },
  {
    id: 'feed',
    icon: <Svg size={20} d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />,
    label: '动态',
  },
  {
    id: 'workbench',
    icon: <Svg size={20} d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />,
    label: '工作台',
  },
  {
    id: 'groupChat',
    icon: (
      <Svg size={20}>
        <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 00-3-3.87" />
        <path d="M16 3.13a4 4 0 010 7.75" />
      </Svg>
    ),
    label: '我的群聊',
  },
  {
    id: 'market',
    icon: (
      <Svg size={20}>
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20" />
        <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
      </Svg>
    ),
    label: '市场',
  },
  {
    id: 'history',
    icon: (
      <Svg size={20}>
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </Svg>
    ),
    label: '记录',
  },
  {
    id: 'trash',
    icon: (
      <Svg size={20}>
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
      </Svg>
    ),
    label: '回收站',
  },
  {
    id: 'mine',
    icon: (
      <Svg size={20}>
        <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </Svg>
    ),
    label: '我的',
  },
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
    ? [...NAV_ITEMS, { id: 'admin', icon: <Svg size={20} d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />, label: '管理' }]
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
          {isActive('workbench') && (
            <>
              {currentView !== 'chat' && currentCard && sessionId && (
                <button type="button" className="sidebar-item sidebar-chat-resume" onClick={() => setView('chat')}>
                  <span className="sidebar-item-icon"><Svg size={20} d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" /></span>
                  <span className="sidebar-item-label">继续对话</span>
                </button>
              )}
            </>
          )}
        </nav>
      )}

      {isVisible && authUser && (
        <div className="sidebar-user-section">
          <div className="sidebar-user-row">
            <button
              type="button"
              className="sidebar-user-link"
              onClick={() => setView('profile')}
              title="个人设置"
            >
              <Avatar name={authUser.username} src={useAppStore.getState().userAvatar} size={50} />
              <span className="sidebar-user-name">{authUser.username}</span>
            </button>
          </div>
          <div className="sidebar-action-row">
            <div className="sidebar-theme-wrap">
              <button
                type="button"
                className="sidebar-action-btn"
                onClick={() => setShowTheme(v => !v)}
                title="切换主题"
              >
                <Svg size={16}><path d="M18.37 2.63L14 7l-1.59-1.59a2 2 0 00-2.82 0L8 7l9 9 1.59-1.59a2 2 0 000-2.82L17 10l4.37-4.37a2 2 0 00-3-3z" /><path d="M9 13l-3 3c-.55.55-.88 1.27-.8 2 .15 1.3-1.9 2.7-2.2 3 .8.3 1.7.5 2.6.5 1.5 0 2.9-.5 3.9-1.5l3-3" /></Svg> 换肤
              </button>
              {showTheme && <ThemePopup onClose={() => setShowTheme(false)} />}
            </div>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={() => setView('voice')}
              title="音色管理"
            >
              <Svg size={16}><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" /><path d="M19 10v2a7 7 0 01-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="8" y1="23" x2="16" y2="23" /></Svg> 音色
            </button>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={() => setView('settings')}
              title="设置"
            >
              <Svg size={16}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" /></Svg> 设置
            </button>
            <button
              type="button"
              className="sidebar-action-btn sidebar-logout-btn"
              onClick={logout}
              title="退出登录"
            >
              <Svg size={16}><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></Svg> 退出
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}

const THEMES = [
  { key: 'aurora',   emoji: '\u{1F4AB}', label: '极光紫' },
  { key: 'milktea',  emoji: '\u{1F375}', label: '抹茶' },
  { key: 'ocean',    emoji: '\u{1F30A}', label: '海盐' },
  { key: 'sakura',   emoji: '\u{1F338}', label: '樱花' },
  { key: 'midnight', emoji: '\u{1F311}', label: '午夜' },
]

function ThemePopup({ onClose }) {
  const [current, setCurrent] = useState(() => {
    try { return localStorage.getItem('charsim-theme') || 'aurora' } catch { return 'aurora' }
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
          <span><Svg size={22}><path d="M4 19.5A2.5 2.5 0 016.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" /></Svg></span>
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
        {pinned
          ? <Svg size={16}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></Svg>
          : <Svg size={16} d="M16 3l3 3L9 16l-4 1 1-4L16 3z" />}
      </button>
    </div>
  )
}
