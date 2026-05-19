import { useState, useEffect } from 'react'

const themes = [
  { key: 'milktea',  label: '\u{1F375} 抹茶' },
  { key: 'ocean',    label: '\u{1F30A} 海盐' },
  { key: 'sakura',   label: '\u{1F338} 樱花' },
  { key: 'midnight', label: '\u{1F319} 午夜' },
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
    <div className="theme-switcher">
      {themes.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => setCurrent(t.key)}
          className={`theme-switch-btn${current === t.key ? ' active' : ''}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
