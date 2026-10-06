import { describe, it, expect } from 'vitest'
import { isTextEntry } from './textEntry'

// P4：键盘层唯一的「是否文字输入框」判断。goalCheck.js 与 useKeyboardFocus.js 共用。
describe('isTextEntry (P4)', () => {
  const el = (tag, type) => {
    const node = document.createElement(tag)
    if (type !== undefined) node.setAttribute('type', type)
    return node
  }

  it('textarea 为真', () => {
    expect(isTextEntry(el('textarea'))).toBe(true)
  })

  it('无 type 的 input 为真（默认 text）', () => {
    expect(isTextEntry(el('input'))).toBe(true)
  })

  it.each(['text', 'password', 'email', 'search', 'tel', 'url', 'number'])(
    'input[type=%s] 为真',
    (type) => {
      expect(isTextEntry(el('input', type))).toBe(true)
    },
  )

  it.each(['checkbox', 'button', 'radio', 'submit', 'file', 'range', 'color', 'date'])(
    'input[type=%s] 为假',
    (type) => {
      expect(isTextEntry(el('input', type))).toBe(false)
    },
  )

  it('非输入元素为假', () => {
    expect(isTextEntry(el('div'))).toBe(false)
    expect(isTextEntry(el('button'))).toBe(false)
    expect(isTextEntry(null)).toBe(false)
    expect(isTextEntry(undefined)).toBe(false)
  })

  it('type 大小写不敏感（HTML 属性归一化为小写）', () => {
    expect(isTextEntry(el('input', 'EMAIL'))).toBe(true)
    expect(isTextEntry(el('input', 'Checkbox'))).toBe(false)
  })
})
