const STORAGE_KEY = 'charsim-theme'

/** @returns {'milktea' | 'ocean' | 'sakura'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'ocean') return 'ocean'
    if (v === 'sakura') return 'sakura'
    return 'milktea'
  } catch {
    return 'milktea'
  }
}

/** @param {'milktea' | 'ocean' | 'sakura'} theme */
export function applyTheme(theme) {
  const valid = ['milktea', 'ocean', 'sakura'].includes(theme) ? theme : 'milktea'
  document.documentElement.className = `theme-${valid}`
  try {
    localStorage.setItem(STORAGE_KEY, valid)
  } catch (err) {
    console.error('[theme] Save theme failed:', err)
  }
}

export function initTheme() {
  applyTheme(getTheme())
}
