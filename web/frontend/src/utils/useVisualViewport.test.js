import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import useVisualViewport from './useVisualViewport'

// 可控的 visualViewport 替身 + 手动的 rAF 队列（hook 用 rAF 合并事件）。
function makeVV(height, offsetTop) {
  const listeners = {}
  return {
    height,
    offsetTop,
    _listeners: listeners,
    addEventListener: (t, fn) => ((listeners[t] ||= []).push(fn)),
    removeEventListener: (t, fn) => (listeners[t] = (listeners[t] || []).filter((f) => f !== fn)),
    emit: (t) => (listeners[t] || []).slice().forEach((fn) => fn()),
  }
}

describe('useVisualViewport (B3 锁)', () => {
  let rafQueue
  let vv
  let scrollTo
  let vvchange

  beforeEach(() => {
    rafQueue = []
    vi.stubGlobal('requestAnimationFrame', (cb) => {
      rafQueue.push(cb)
      return rafQueue.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    scrollTo = vi.fn()
    vi.stubGlobal('scrollTo', scrollTo)

    vv = makeVV(800, 0)
    window.visualViewport = vv
    document.documentElement.removeAttribute('data-kbd')
    document.documentElement.style.removeProperty('--vvh')

    vvchange = vi.fn()
    window.addEventListener('vvchange', vvchange)
  })

  afterEach(() => {
    window.removeEventListener('vvchange', vvchange)
    document.documentElement.removeAttribute('data-kbd')
    document.documentElement.style.removeProperty('--vvh')
    delete window.visualViewport
    vi.unstubAllGlobals()
  })

  const flushRaf = () => {
    const cbs = rafQueue
    rafQueue = []
    cbs.forEach((cb) => cb())
  }
  const vvh = () => document.documentElement.style.getPropertyValue('--vvh')

  it('没有 data-kbd 时照旧：写 --vvh、offsetTop>0 时 scrollTo、高度变小派发 vvchange', () => {
    renderHook(() => useVisualViewport())
    expect(vvh()).toBe('800px')

    vv.height = 400
    vv.offsetTop = 120
    vv.emit('resize')
    flushRaf()

    expect(vvh()).toBe('400px')
    expect(scrollTo).toHaveBeenCalledWith(0, 0)
    expect(vvchange).toHaveBeenCalledTimes(1)
  })

  it('有 data-kbd="native" 时三件事都不做', () => {
    document.documentElement.setAttribute('data-kbd', 'native')
    renderHook(() => useVisualViewport())

    // 初始 sync 不写 --vvh
    expect(vvh()).toBe('')

    vv.height = 400
    vv.offsetTop = 120
    vv.emit('resize')
    vv.emit('scroll')
    flushRaf()

    expect(vvh()).toBe('')
    expect(scrollTo).not.toHaveBeenCalled()
    expect(vvchange).not.toHaveBeenCalled()
  })
})
