/**
 * 「是否文字输入框」——键盘层唯一的一处判断（P4）。
 * goalCheck.js 与 useKeyboardFocus.js 共用，别处不要再写一份。
 *
 * 覆盖登录页会用到的 password / email / search / tel / url / number。
 */
const TEXT_INPUT_TYPES = new Set([
  'text',
  'password',
  'email',
  'search',
  'tel',
  'url',
  'number',
])

export function isTextEntry(el) {
  if (!el || !el.tagName) return false
  if (el.tagName === 'TEXTAREA') return true
  if (el.tagName !== 'INPUT') return false
  return TEXT_INPUT_TYPES.has((el.getAttribute('type') || 'text').toLowerCase())
}
