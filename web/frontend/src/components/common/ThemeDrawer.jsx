import { useState } from 'react'
import { Popup } from 'antd-mobile'
import { THEMES } from '../../utils/themes'
import { applyTheme, getTheme } from '../../utils/theme'
import { Check, Close } from './Icon'

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
          <Close size={18} />
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
