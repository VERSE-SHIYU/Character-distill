import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import useNativeKeyboardPage from './useNativeKeyboardPage'
import { MODE_KEY } from './keyboardMode'

const setViewport = (width, mobile) => {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true })
  window.matchMedia = (q) => ({
    matches: mobile,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })
}

const kbdAttr = () => document.documentElement.getAttribute('data-kbd')

describe('useNativeKeyboardPage (B2)', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-kbd')
    document.documentElement.style.removeProperty('--vvh')
  })

  afterEach(() => {
    document.documentElement.removeAttribute('data-kbd')
  })

  it('native + 手机宽度：挂载时加 data-kbd 并清掉 --vvh', () => {
    localStorage.setItem(MODE_KEY, 'native')
    setViewport(390, true)
    document.documentElement.style.setProperty('--vvh', '500px')

    const { result } = renderHook(() => useNativeKeyboardPage())

    expect(result.current).toBe(true)
    expect(kbdAttr()).toBe('native')
    expect(document.documentElement.style.getPropertyValue('--vvh')).toBe('')
  })

  it('卸载时移除属性', () => {
    localStorage.setItem(MODE_KEY, 'native')
    setViewport(390, true)
    const { unmount } = renderHook(() => useNativeKeyboardPage())
    expect(kbdAttr()).toBe('native')
    unmount()
    expect(kbdAttr()).toBeNull()
  })

  it('legacy：不加属性', () => {
    localStorage.setItem(MODE_KEY, 'legacy')
    setViewport(390, true)
    const { result } = renderHook(() => useNativeKeyboardPage())
    expect(result.current).toBe(false)
    expect(kbdAttr()).toBeNull()
  })

  it('native 但桌面宽度：不加属性', () => {
    localStorage.setItem(MODE_KEY, 'native')
    setViewport(1280, false)
    const { result } = renderHook(() => useNativeKeyboardPage())
    expect(result.current).toBe(false)
    expect(kbdAttr()).toBeNull()
  })

  it('默认（未开开关）：不加属性', () => {
    setViewport(390, true)
    const { result } = renderHook(() => useNativeKeyboardPage())
    expect(result.current).toBe(false)
    expect(kbdAttr()).toBeNull()
  })
})
