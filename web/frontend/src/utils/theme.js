const STORAGE_KEY = 'charsim-theme'

/** @returns {'milktea' | 'ocean' | 'sakura' | 'midnight'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'ocean') return 'ocean'
    if (v === 'sakura') return 'sakura'
    if (v === 'midnight') return 'midnight'
    return 'milktea'
  } catch {
    return 'milktea'
  }
}

/** @param {'milktea' | 'ocean' | 'sakura' | 'midnight'} theme */
export function applyTheme(theme) {
  const valid = ['milktea', 'ocean', 'sakura', 'midnight'].includes(theme) ? theme : 'milktea'
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
