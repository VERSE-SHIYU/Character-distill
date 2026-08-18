import { useEffect, useState, useCallback, useRef } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'
import GlobalSearchBox from './common/GlobalSearchBox'
import { displayName } from '../utils/displayName'
import { THEMES } from '../utils/themes'
import { applyTheme, getTheme, applyFontDisplay, getFontDisplay } from '../utils/theme'
import { Check, Home, Edit3, Users, Clock, Globe, Trash2, Heart, User, Shield, MessageSquare, Palette, Mic, Settings, LogIn, Book, Close } from './common/Icon'

const NAV_ITEMS = [
  {
    id: 'home',
    icon: <Home size={20} />,
    label: '首页',
  },
  {
    id: 'workbench',
    icon: <Edit3 size={20} />,
    label: '创作',
  },
  {
    id: 'groupChat',
    icon: <Users size={20} />,
    label: '群聊',
  },
  {
    id: 'history',
    icon: <Clock size={20} />,
    label: '历史',
  },
  {
    id: 'market',
    icon: <Globe size={20} />,
    label: '市场',
  },
  {
    id: 'trash',
    icon: <Trash2 size={20} />,
    label: '回收站',
  },
  {
    id: 'feed',
    icon: <Heart size={20} />,
    label: '动态',
  },
  {
    id: 'mine',
    icon: <User size={20} />,
    label: '我的',
  },
]

export default function Sidebar({ open, pinned, onShow, onHide, onTogglePin }) {
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const navigateTo = useAppStore((s) => s.navigateTo)
  const startChat = useAppStore((s) => s.startChat)
  const authUser = useAppStore((s) => s.authUser)
  const logout = useAppStore((s) => s.logout)
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)
  const unreadTotal = useAppStore((s) => s.unreadTotal)
  const [showTheme, setShowTheme] = useState(false)

  const isVisible = open || pinned

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
      case 'workbench': setView('text'); break   // tab-level: sidebar nav
      case 'trash': setView('trash'); break       // tab-level: sidebar nav
      case 'mine': setView('mine'); break          // tab-level: sidebar nav
      default: setView(id)                         // tab-level: sidebar nav
    }
  }, [setView])

  const navItems = authUser?.is_admin
    ? [...NAV_ITEMS, { id: 'admin', icon: <Shield size={20} />, label: '管理' }]
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

      {/* 搜索框 — 仅展开态显示 */}
      {isVisible && <GlobalSearchBox />}

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
              {id === 'history' && unreadTotal > 0 && (
                <span className="sidebar-item-badge">{unreadTotal}</span>
              )}
            </button>
          ))}
          {isActive('workbench') && (
            <>
              {currentView !== 'chat' && currentCard && sessionId && (
                <button type="button" className="sidebar-item sidebar-chat-resume" onClick={() => navigateTo('chat')}>
                  <span className="sidebar-item-icon"><MessageSquare size={20} /></span>
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
              onClick={() => navigateTo('profile')}
              title="个人设置"
            >
              <Avatar name={displayName(authUser) || '?'} src={useAppStore.getState().userAvatar} size={60} />
              <span className="sidebar-user-name">{displayName(authUser)}</span>
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
                <Palette size={16} /> 换肤
              </button>
              {showTheme && <ThemePopup onClose={() => setShowTheme(false)} />}
            </div>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={() => navigateTo('voice')}
              title="音色管理"
            >
              <Mic size={16} /> 音色
            </button>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={() => navigateTo('settings')}
              title="设置"
            >
              <Settings size={16} /> 设置
            </button>
            <button
              type="button"
              className="sidebar-action-btn"
              onClick={() => navigateTo('legal')}
              title="法律条款"
            >
              <Shield size={16} /> 条款
            </button>
            <button
              type="button"
              className="sidebar-action-btn sidebar-logout-btn"
              onClick={logout}
              title="退出登录"
            >
              <LogIn size={16} /> 退出
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}

function ThemePopup({ onClose }) {
  const [current, setCurrent] = useState(() => getTheme())
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current?.closest('.sidebar-theme-wrap')?.contains(e.target)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const apply = (key) => {
    applyTheme(key)
    setCurrent(key)
  }

  const [fontMode, setFontMode] = useState(() => getFontDisplay())
  const applyFont = (mode) => {
    applyFontDisplay(mode)
    setFontMode(mode)
  }

  return (
    <div className="theme-popup" ref={ref}>
      {THEMES.map(t => (
        <button key={t.key} type="button"
          className={`theme-popup-item${current === t.key ? ' active' : ''}`}
          onClick={() => apply(t.key)}>
          <span className="theme-popup-emoji">{t.emoji}</span>
          <span className="theme-popup-label">{t.label}</span>
          {current === t.key && <span className="theme-popup-check"><Check size={12} /></span>}
        </button>
      ))}
      <div className="theme-popup-divider" />
      <div className="theme-popup-fontrow">
        <span className="theme-popup-fontlabel">标题字体</span>
        <div className="theme-popup-fontbtns">
          <button type="button" className={`theme-popup-fontbtn${fontMode === 'sans' ? ' active' : ''}`} onClick={() => applyFont('sans')}>系统黑体</button>
          <button type="button" className={`theme-popup-fontbtn${fontMode === 'serif' ? ' active' : ''}`} onClick={() => applyFont('serif')}>宋体标题</button>
        </div>
      </div>
    </div>
  )
}

function SidebarHeader({ pinned, onTogglePin }) {
  return (
    <div className="sidebar-header">
      <div className="sidebar-logo-wrap">
        <div className="sidebar-logo">
          <span><Book size={22} /></span>
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
          ? <Close size={16} />
          : <Edit3 size={16} />}
      </button>
    </div>
  )
}
