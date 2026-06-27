const STORAGE_KEY = 'charsim-theme'

/** @returns {'aurora' | 'milktea' | 'ocean' | 'sakura' | 'midnight'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'aurora') return 'aurora'
    if (v === 'ocean') return 'ocean'
    if (v === 'sakura') return 'sakura'
    if (v === 'midnight') return 'midnight'
    return 'aurora'
  } catch {
    return 'aurora'
  }
}

/** @param {'aurora' | 'milktea' | 'ocean' | 'sakura' | 'midnight'} theme */
export function applyTheme(theme) {
  const valid = ['aurora', 'milktea', 'ocean', 'sakura', 'midnight'].includes(theme) ? theme : 'aurora'
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
