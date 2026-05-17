const STORAGE_KEY = 'charsim-theme'

/** @returns {'milktea' | 'ocean'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'ocean' ? 'ocean' : 'milktea'
  } catch {
    return 'milktea'
  }
}

/** @param {'milktea' | 'ocean'} theme */
export function applyTheme(theme) {
  const t = theme === 'ocean' ? 'ocean' : 'milktea'
  document.documentElement.className = `theme-${t}`
  try {
    localStorage.setItem(STORAGE_KEY, t)
  } catch (err) {
    console.error('[theme] Save theme failed:', err)
  }
}

export function initTheme() {
  applyTheme(getTheme())
}
