import { useEffect, useState, useCallback } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'
import ThemeSwitcher from './ThemeSwitcher'
import { fetchWithTimeout } from '../api/client'

const NAV_GROUPS = [
  {
    label: '工作流',
    items: [
      { id: 'home', icon: '\u{1F3E0}', label: '主页' },
      { id: 'text', icon: '\u{1F4C1}', label: '文本管理' },
      { id: 'character', icon: '\u{1F3AD}', label: '角色管理' },
      { id: 'chat', icon: '\u{1F4AC}', label: '聊天' },
    ],
  },
  {
    label: '社区',
    items: [
      { id: 'market', icon: '\u{1F30D}', label: '角色市场' },
      { id: 'messages', icon: '\u{1F4E8}', label: '私信' },
    ],
  },
  {
    label: '管理',
    items: [
      { id: 'history', icon: '\u{1F4DA}', label: '历史' },
      { id: 'voice', icon: '\u{1F399}', label: '音色管理' },
      { id: 'profile', icon: '\u{1F464}', label: '个人资料' },
      { id: 'settings', icon: '⚙️', label: '设置' },
    ],
  },
]

export default function Sidebar({ open, pinned, onShow, onHide, onTogglePin }) {
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const startChat = useAppStore((s) => s.startChat)
  const currentTextTitle = useAppStore((s) => s.currentTextTitle)
  const currentCard = useAppStore((s) => s.currentCard)
  const texts = useAppStore((s) => s.texts)
  const cards = useAppStore((s) => s.cards)
  const authUser = useAppStore((s) => s.authUser)
  const logout = useAppStore((s) => s.logout)
  const [sessionCount, setSessionCount] = useState(0)
  const [unreadCount, setUnreadCount] = useState(0)

  const hasTexts = texts.length > 0
  const isVisible = open || pinned

  useEffect(() => {
    fetchWithTimeout('/api/history/list?page=1&page_size=1')
      .then((r) => r.json())
      .then((d) => setSessionCount(d.total ?? 0))
      .catch(() => {})
  }, [])

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

  function badge(id) {
    switch (id) {
      case 'text': return texts.length > 0 ? String(texts.length) : null
      case 'character': return cards.length > 0 ? String(cards.length) : null
      case 'history': return sessionCount > 0 ? String(sessionCount) : null
      case 'messages': return unreadCount > 0 ? String(unreadCount) : null
      default: return null
    }
  }

  function isDisabled(id) {
    if (id === 'text') return false
    if (id === 'home' || id === 'settings' || id === 'history' || id === 'profile' || id === 'market' || id === 'messages') return false
    return !hasTexts && id !== 'home' && id !== 'settings' && id !== 'history' && id !== 'profile'
  }

  const handleNav = useCallback((id) => {
    if (id === 'chat') {
      if (currentCard) {
        setView('character')
        return
      }
      alert('请先选择角色并开始对话')
      return
    }
    setView(id)
  }, [setView, currentCard])

  const navGroups = authUser?.is_admin
    ? NAV_GROUPS.map((g) =>
        g.label === '管理'
          ? { ...g, items: [...g.items, { id: 'admin', icon: '\u{1F6E1}', label: '管理后台' }] }
          : g,
      )
    : NAV_GROUPS

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
        <div className="sidebar-breadcrumb">
          <span className="sidebar-breadcrumb-link" onClick={() => setView('home')}>
            {'📁'} 文本管理
          </span>
          {currentTextTitle && (
            <>
              <span className="sidebar-breadcrumb-sep">{'›'}</span>
              <span className="sidebar-breadcrumb-text">{currentTextTitle}</span>
            </>
          )}
          {currentView === 'character' && (
            <>
              <span className="sidebar-breadcrumb-sep">{'›'}</span>
              <span className="sidebar-breadcrumb-text">角色管理</span>
            </>
          )}
          {currentView === 'chat' && (
            <>
              <span className="sidebar-breadcrumb-sep">{'›'}</span>
              <span className="sidebar-breadcrumb-text">对话</span>
            </>
          )}
        </div>
      )}

      {isVisible && (
        <nav className="sidebar-nav">
          {navGroups.map((group, gi) => (
            <div key={group.label}>
              {gi > 0 && <div style={{ height: 1, background: 'var(--glass-border)', margin: '8px 12px' }} />}
              {group.items.map(({ id, icon, label }) => {
                const disabled = isDisabled(id)
                const count = badge(id)
                return (
                  <button
                    key={id}
                    type="button"
                    className={`sidebar-item${currentView === id ? ' active' : ''}${disabled ? ' sidebar-item-disabled' : ''}`}
                    onClick={() => handleNav(id)}
                  >
                    <span className="sidebar-item-icon">{icon}</span>
                    <span className="sidebar-item-label">{label}</span>
                    {count !== null && <span className="sidebar-item-badge">{count}</span>}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>
      )}

      {currentCard && isVisible && (
        <button
          type="button"
          className="sidebar-char-card"
          onClick={() => setView('character')}
          title="查看角色详情"
        >
          <Avatar name={currentCard.name} size={40} />
          <div className="sidebar-char-info">
            <div className="sidebar-char-name">{currentCard.name}</div>
            {currentCard.identity && (
              <div className="sidebar-char-meta">{currentCard.identity}</div>
            )}
          </div>
        </button>
      )}

      {isVisible && <ThemeSwitcher />}

      {isVisible && authUser && (
        <div className="sidebar-user-info">
          <span className="sidebar-user-name">{authUser.username}</span>
          <button className="sidebar-logout-btn" onClick={logout} title="退出登录">
            退出
          </button>
        </div>
      )}
    </aside>
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
