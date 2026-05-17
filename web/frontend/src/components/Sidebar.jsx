import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'

const NAV_ITEMS = [
  { id: 'home', icon: '\u{1F3E0}', label: '\u4e3b\u9875' },
  { id: 'text', icon: '\u{1F4C1}', label: '\u6587\u672c\u7ba1\u7406' },
  { id: 'character', icon: '\u{1F464}', label: '\u89d2\u8272\u7ba1\u7406' },
  { id: 'chat', icon: '\u{1F4AC}', label: '\u804a\u5929' },
  { id: 'history', icon: '\u{1F4DA}', label: '\u5386\u53f2' },
  { id: 'settings', icon: '\u2699\uFE0F', label: '\u8bbe\u7f6e' },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const currentCard = useAppStore((s) => s.currentCard)

  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <SidebarHeader collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />

      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ id, icon, label }) => (
          <button
            key={id}
            type="button"
            className={`sidebar-item${currentView === id ? ' active' : ''}`}
            onClick={() => setView(id)}
            title={collapsed ? label : undefined}
          >
            <span className="sidebar-item-icon">{icon}</span>
            <span className="sidebar-item-label">{label}</span>
          </button>
        ))}
      </nav>

      {currentCard && (
        <button
          type="button"
          className="sidebar-char-card"
          onClick={() => setView('chat')}
          title={collapsed ? currentCard.name : '\u8fdb\u5165\u804a\u5929'}
        >
          <Avatar name={currentCard.name} size={collapsed ? 36 : 40} />
          {!collapsed && (
            <div className="sidebar-char-info">
              <div className="sidebar-char-name">{currentCard.name}</div>
              {currentCard.identity && (
                <div className="sidebar-char-meta">{currentCard.identity}</div>
              )}
            </div>
          )}
        </button>
      )}
    </aside>
  )
}

function SidebarHeader({ collapsed, onToggle }) {
  return (
    <div className="sidebar-header">
      <div className="sidebar-logo-wrap">
        <div className="sidebar-logo">
          <span>{'\u{1F4D6}'}</span>
          {!collapsed && <span className="sidebar-logo-text">CharSim</span>}
        </div>
        {!collapsed && (
          <div className="sidebar-sub">
            {'\u89d2\u8272\u84b8\u998f\u4e0e\u6c89\u6d78\u5f0f\u5bf9\u8bdd'}
          </div>
        )}
      </div>
      <button
        type="button"
        className="sidebar-collapse-btn"
        onClick={onToggle}
        aria-label={collapsed ? '\u5c55\u5f00\u4fa7\u8fb9\u680f' : '\u6536\u8d77\u4fa7\u8fb9\u680f'}
        title={collapsed ? '\u5c55\u5f00' : '\u6536\u8d77'}
      >
        {collapsed ? '\u00bb' : '\u00ab'}
      </button>
    </div>
  )
}
