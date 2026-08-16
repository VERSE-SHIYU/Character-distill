import { useState } from 'react'
import { Popup } from 'antd-mobile'
import { THEMES } from '../../utils/themes'
import { applyTheme, getTheme } from '../../utils/theme'
import { Check } from './Icon'

export default function ThemeDrawer({ open, onClose }) {
  const [currentTheme, setCurrentTheme] = useState(() => getTheme())

  return (
    <Popup
      visible={open}
      onMaskClick={onClose}
      position="bottom"
      bodyClassName="mine-theme-drawer"
    >
      <div className="mine-theme-drawer-header">
        <span>选择主题</span>
        <button type="button" className="mine-theme-drawer-close" onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div className="mine-theme-grid">
        {THEMES.map(t => (
          <button
            key={t.key}
            type="button"
            className={`mine-theme-item${currentTheme === t.key ? ' active' : ''}`}
            onClick={() => {
              applyTheme(t.key)
              setCurrentTheme(t.key)
            }}
          >
            <span className="mine-theme-emoji">{t.emoji}</span>
            <span className="mine-theme-label">{t.label}</span>
            {currentTheme === t.key && <span className="mine-theme-check"><Check size={12} /></span>}
          </button>
        ))}
      </div>
    </Popup>
  )
}
