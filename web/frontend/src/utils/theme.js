const STORAGE_KEY = 'charsim-theme'

/** @returns {'light' | 'dark'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/** @param {'light' | 'dark'} theme */
export function applyTheme(theme) {
  const t = theme === 'dark' ? 'dark' : 'light'
  document.documentElement.setAttribute('data-theme', t)
  try {
    localStorage.setItem(STORAGE_KEY, t)
  } catch (err) {
    console.error('[theme] Save theme failed:', err)
  }
}

export function initTheme() {
  applyTheme(getTheme())
}
