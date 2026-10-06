import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import useKeyboardFocus from './useKeyboardFocus'

export const UAS = {
  iosSafari:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  iosWechat:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN',
  androidChrome:
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
}

function makeVV(height) {
  const listeners = {}
  return {
    height,
    offsetTop: 0,
    addEventListener: (t, fn) => ((listeners[t] ||= []).push(fn)),
    removeEventListener: (t, fn) => (listeners[t] = (listeners[t] || []).filter((f) => f !== fn)),
    emit: (t) => (listeners[t] || []).slice().forEach((fn) => fn()),
  }
}

describe('useKeyboardFocus (B5/B7/B8)', () => {
  let rootRef
  let ta
  let checkbox
  let origInnerHeight

  beforeEach(() => {
    vi.useFakeTimers()
    origInnerHeight = window.innerHeight
    document.body.innerHTML = ''
    document.body.className = ''

    const root = document.createElement('div')
    ta = document.createElement('textarea')
    checkbox = document.createElement('input')
    checkbox.setAttribute('type', 'checkbox')
    root.append(ta, checkbox)
    document.body.appendChild(root)
    rootRef = { current: root }

    ta.scrollIntoView = vi.fn()
    document.body.scrollIntoView = vi.fn()
    window.visualViewport = makeVV(800)
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.className = ''
    delete window.visualViewport
    Object.defineProperty(window, 'innerHeight', {
      value: origInnerHeight,
      configurable: true,
      writable: true,
    })
  })

  const renderFocus = ({
    enabled = true,
    ua = UAS.iosSafari,
    onFocus = vi.fn(),
    getRiseTarget,
  } = {}) =>
    renderHook(() => useKeyboardFocus({ rootRef, enabled, ua, onFocus, getRiseTarget }))

  const focusing = () => document.body.classList.contains('kbd-focusing')

  it('聚焦文字输入框加 kbd-focusing，失焦后下一个事件循环移除', () => {
    renderFocus()
    act(() => ta.focus())
    expect(focusing()).toBe(true)

    act(() => ta.blur())
    // 还没到下一个事件循环，类还在
    expect(focusing()).toBe(true)
    act(() => vi.advanceTimersByTime(0))
    expect(focusing()).toBe(false)
  })

  it('顺序：聚焦回调执行时 kbd-focusing 已经在 body 上', () => {
    const seen = []
    renderFocus({ onFocus: () => seen.push(focusing()) })
    act(() => ta.focus())
    expect(seen).toEqual([true])
  })

  it('从一个文字输入框换到另一个时不闪掉', () => {
    const ta2 = document.createElement('textarea')
    rootRef.current.appendChild(ta2)
    renderFocus()

    act(() => ta.focus())
    act(() => ta.blur())
    act(() => ta2.focus()) // 在移除定时器触发前又聚焦
    act(() => vi.advanceTimersByTime(0))
    expect(focusing()).toBe(true)
  })

  it('非文字控件不触发', () => {
    const onFocus = vi.fn()
    renderFocus({ onFocus })
    act(() => checkbox.focus())
    act(() => vi.advanceTimersByTime(0))
    expect(focusing()).toBe(false)
    expect(onFocus).not.toHaveBeenCalled()
  })

  it('卸载时移除 kbd-focusing', () => {
    const { unmount } = renderFocus()
    act(() => ta.focus())
    expect(focusing()).toBe(true)
    unmount()
    expect(focusing()).toBe(false)
  })

  it('enabled=false 时不接管', () => {
    renderFocus({ enabled: false })
    act(() => ta.focus())
    expect(focusing()).toBe(false)
  })

  // B8 / D5 —— UA 三分支
  it('iOS Safari 的 UA：聚焦不滚', () => {
    renderFocus({ ua: UAS.iosSafari })
    act(() => ta.focus())
    act(() => vi.advanceTimersByTime(1500))
    expect(ta.scrollIntoView).not.toHaveBeenCalled()
  })

  it('iOS 微信的 UA：300ms、1000ms 各滚一次', () => {
    renderFocus({ ua: UAS.iosWechat })
    act(() => ta.focus())
    act(() => vi.advanceTimersByTime(300))
    expect(ta.scrollIntoView).toHaveBeenCalledTimes(1)
    act(() => vi.advanceTimersByTime(700))
    expect(ta.scrollIntoView).toHaveBeenCalledTimes(2)
  })

  it('安卓 Chrome 的 UA：同样滚', () => {
    renderFocus({ ua: UAS.androidChrome })
    act(() => ta.focus())
    act(() => vi.advanceTimersByTime(1000))
    expect(ta.scrollIntoView).toHaveBeenCalledTimes(2)
  })

  it('iOS 非 Safari（微信）失焦时页面滚回顶部；iOS Safari 不滚', () => {
    const wechat = renderFocus({ ua: UAS.iosWechat })
    act(() => ta.focus())
    act(() => ta.blur())
    act(() => vi.advanceTimersByTime(0))
    expect(document.body.scrollIntoView).toHaveBeenCalled()
    wechat.unmount()

    document.body.scrollIntoView.mockClear()
    renderFocus({ ua: UAS.iosSafari })
    act(() => ta.focus())
    act(() => ta.blur())
    act(() => vi.advanceTimersByTime(0))
    expect(document.body.scrollIntoView).not.toHaveBeenCalled()
  })

  // B8 / D6 —— iOS 键盘收起但未失焦时让它失焦
  it('iOS：可视视口变高（键盘收起）且仍聚焦时，让输入框失焦', () => {
    renderFocus({ ua: UAS.iosSafari })
    act(() => ta.focus())
    const blurSpy = vi.spyOn(ta, 'blur')

    window.visualViewport.height = window.innerHeight // 键盘收起
    act(() => window.visualViewport.emit('resize'))

    expect(blurSpy).toHaveBeenCalled()
  })

  // P7：winHeight 是「只增不减」的窗口高度，不是每次比较时现取的 innerHeight。
  const setInnerHeight = (h) =>
    Object.defineProperty(window, 'innerHeight', { value: h, configurable: true, writable: true })

  it('P7-1：键盘还开着（可视高度 < winHeight）触发 resize 不失焦', () => {
    setInnerHeight(800) // winHeight 安装时记为 800
    renderFocus({ ua: UAS.iosSafari })
    act(() => ta.focus())
    const blurSpy = vi.spyOn(ta, 'blur')

    window.visualViewport.height = 400 // 键盘弹起，可视高度变小
    act(() => window.visualViewport.emit('resize'))

    expect(blurSpy).not.toHaveBeenCalled()
  })

  it('P7-2：innerHeight 短暂变小到等于可视高度时不误判失焦', () => {
    setInnerHeight(800)
    renderFocus({ ua: UAS.iosSafari })
    act(() => ta.focus())
    const blurSpy = vi.spyOn(ta, 'blur')

    window.visualViewport.height = 400
    setInnerHeight(400) // iOS 26 弹起瞬间 innerHeight 短暂变小
    act(() => window.visualViewport.emit('resize'))

    expect(blurSpy).not.toHaveBeenCalled()
  })

  // P6：滚进视野的目标由页面给出（第二个参数是输入区，不是 input 本身）。
  it('P6：传了 getRiseTarget 时，滚动打在返回的元素上，输入框自己不被滚', () => {
    const target = document.createElement('div')
    target.scrollIntoView = vi.fn()
    renderFocus({ ua: UAS.androidChrome, getRiseTarget: () => target })
    act(() => ta.focus())
    act(() => vi.advanceTimersByTime(1000))

    expect(target.scrollIntoView).toHaveBeenCalledTimes(2)
    expect(target.scrollIntoView).toHaveBeenCalledWith(false)
    expect(ta.scrollIntoView).not.toHaveBeenCalled()
  })

  it('P6：getRiseTarget 返回 null 时回落到输入框本身', () => {
    renderFocus({ ua: UAS.androidChrome, getRiseTarget: () => null })
    act(() => ta.focus())
    act(() => vi.advanceTimersByTime(1000))
    expect(ta.scrollIntoView).toHaveBeenCalledTimes(2)
  })
})
