const STORAGE_KEY = 'charsim-theme'
const FONT_STORAGE_KEY = 'charsim-font'

/** @returns {'aurora' | 'milktea' | 'ocean' | 'sakura' | 'midnight' | 'galaxy'} */
export function getTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'aurora') return 'aurora'
    if (v === 'milktea') return 'milktea'
    if (v === 'ocean') return 'ocean'
    if (v === 'sakura') return 'sakura'
    if (v === 'midnight') return 'midnight'
    if (v === 'galaxy') return 'galaxy'
    return 'aurora'
  } catch {
    return 'aurora'
  }
}

/** @param {'aurora' | 'milktea' | 'ocean' | 'sakura' | 'midnight' | 'galaxy'} theme */
export function applyTheme(theme) {
  const valid = ['aurora', 'milktea', 'ocean', 'sakura', 'midnight', 'galaxy'].includes(theme) ? theme : 'aurora'
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

/** @returns {'serif' | 'sans'} 标题/角色名显示字体（系统宋体 or 系统黑体） */
export function getFontDisplay() {
  try {
    return localStorage.getItem(FONT_STORAGE_KEY) === 'serif' ? 'serif' : 'sans'
  } catch {
    return 'sans'
  }
}

/** @param {'serif' | 'sans'} mode */
export function applyFontDisplay(mode) {
  const serif = mode === 'serif'
  document.documentElement.classList.toggle('serif-display', serif)
  try {
    localStorage.setItem(FONT_STORAGE_KEY, serif ? 'serif' : 'sans')
  } catch (err) {
    console.error('[theme] Save font display failed:', err)
  }
}

export function initFontDisplay() {
  applyFontDisplay(getFontDisplay())
}
