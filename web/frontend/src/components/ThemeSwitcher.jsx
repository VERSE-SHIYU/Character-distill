import { useState, useEffect } from 'react'

const themes = [
  { key: 'milktea', label: '\u{1F375} 奶油抹茶' },
  { key: 'ocean', label: '\u{1F30A} 蓝色海盐' },
]

export default function ThemeSwitcher() {
  const [current, setCurrent] = useState(() => {
    try { return localStorage.getItem('charsim-theme') || 'milktea' }
    catch { return 'milktea' }
  })

  useEffect(() => {
    document.documentElement.className = `theme-${current}`
    try { localStorage.setItem('charsim-theme', current) }
    catch { /* noop */ }
  }, [current])

  return (
    <div className="theme-switcher" style={{ display: 'flex', gap: 4, padding: '4px 10px 10px' }}>
      {themes.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => setCurrent(t.key)}
          className={`sidebar-item${current === t.key ? ' active' : ''}`}
          style={{ flex: 1, justifyContent: 'center', fontSize: 12 }}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
