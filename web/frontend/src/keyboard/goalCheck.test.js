import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  MEASURE_AT_MS,
  evaluateGoalCheck,
  formatReport,
  installGoalCheck,
} from './goalCheck'

const rect = (top, bottom) => ({ top, bottom, height: bottom - top })

// visualViewport stub that also behaves as an EventTarget, so installGoalCheck
// can subscribe to scroll/resize. `emit` drives the listeners by hand.
const vv = (offsetTop, height) => {
  const listeners = {}
  return {
    offsetTop,
    height,
    _listeners: listeners,
    addEventListener: (t, fn) => {
      ;(listeners[t] ||= []).push(fn)
    },
    removeEventListener: (t, fn) => {
      listeners[t] = (listeners[t] || []).filter((f) => f !== fn)
    },
    emit: (t) => (listeners[t] || []).slice().forEach((fn) => fn()),
  }
}

describe('evaluateGoalCheck — C1 输入栏在键盘上方', () => {
  it('passes when the input bar lies fully inside the visual viewport', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(700, 780),
      lastRect: rect(600, 690),
      listBottomPadding: 80,
      vv: vv(0, 800),
      windowHeight: 800,
    })
    expect(r.c1).toBe(true)
  })

  it('fails when the input bar bottom is below the visible area', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(700, 820),
      lastRect: rect(600, 690),
      listBottomPadding: 80,
      vv: vv(0, 800),
      windowHeight: 800,
    })
    expect(r.c1).toBe(false)
  })

  it('fails when the input bar top is above the visible area (page pushed up past it)', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(120, 200),
      lastRect: rect(20, 110),
      listBottomPadding: 80,
      vv: vv(200, 800),
      windowHeight: 800,
    })
    expect(r.c1).toBe(false)
  })
})

describe('evaluateGoalCheck — C2 最后一条消息贴着输入栏', () => {
  const base = {
    inputRect: rect(700, 780),
    listBottomPadding: 80,
    vv: vv(0, 800),
    windowHeight: 800,
  }

  it('passes at gap 0', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(600, 700) }).c2).toBe(true)
  })

  it('passes at gap == padding + 24 (upper bound inclusive)', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(600, 596) }).c2).toBe(true)
  })

  it('fails just past the upper bound (blank gap too big)', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(600, 595) }).c2).toBe(false)
  })

  it('fails when the input bar is above the last message (negative gap)', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(790, 850) }).c2).toBe(false)
  })
})

describe('evaluateGoalCheck — C3 消息区不是空白', () => {
  const base = {
    inputRect: rect(700, 780),
    listBottomPadding: 80,
    vv: vv(0, 800),
    windowHeight: 800,
  }

  it('passes when at least 20px of the last message is visible', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(600, 710) }).c3).toBe(true)
  })

  it('fails when only 19px of the last message is visible', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(600, 619) }).c3).toBe(false)
  })

  it('fails when the last message is entirely above the viewport', () => {
    expect(evaluateGoalCheck({ ...base, lastRect: rect(-100, -10) }).c3).toBe(false)
  })
})

// P3：判据里不把 offsetTop 算进去会翻转结论的样本。单个样本同时覆盖
// C1（视觉区下界）与 C3（末条可见高度）。
describe('evaluateGoalCheck — offsetTop 参与判据（P3）', () => {
  const inputRect = rect(700, 780)
  const lastRect = rect(600, 690)

  it('offsetTop>0 时三项全过；忽略 offsetTop 会把 C1、C3 翻成不过', () => {
    const withOffset = evaluateGoalCheck({
      inputRect,
      lastRect,
      listBottomPadding: 80,
      vv: vv(200, 600),
      windowHeight: 600,
    })
    expect(withOffset).toMatchObject({ c1: true, c2: true, c3: true })

    const ignored = evaluateGoalCheck({
      inputRect,
      lastRect,
      listBottomPadding: 80,
      vv: vv(0, 600),
      windowHeight: 600,
    })
    expect(ignored).toMatchObject({ c1: false, c3: false })
  })
})

// 边界（含等号）——判据用 >= / <=，写成 > / < 会在这里变红。
describe('evaluateGoalCheck — 边界（含等号）', () => {
  it('C1：输入栏顶与视觉区顶重合算过', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(0, 80),
      lastRect: rect(-100, -10),
      listBottomPadding: 0,
      vv: vv(0, 800),
      windowHeight: 800,
    })
    expect(r.c1).toBe(true)
  })

  it('C1：输入栏底与视觉区底重合算过', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(720, 800),
      lastRect: rect(600, 690),
      listBottomPadding: 0,
      vv: vv(0, 800),
      windowHeight: 800,
    })
    expect(r.c1).toBe(true)
  })

  it('C3：恰好 20px 可见算过', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(700, 780),
      lastRect: rect(600, 620),
      listBottomPadding: 80,
      vv: vv(0, 800),
      windowHeight: 800,
    })
    expect(r.c3).toBe(true)
  })
})

describe('evaluateGoalCheck — reported extras', () => {
  it('echoes the pushed-up distance, view height and window height', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(300, 380),
      lastRect: rect(200, 290),
      listBottomPadding: 80,
      vv: vv(150, 600),
      windowHeight: 800,
    })
    expect(r.offsetTop).toBe(150)
    expect(r.viewHeight).toBe(600)
    expect(r.windowHeight).toBe(800)
  })
})

describe('formatReport', () => {
  it('labels the sample and shows pass/fail for each criterion', () => {
    const line = formatReport('800ms', {
      c1: true,
      c2: false,
      c3: true,
      inputTop: 700,
      inputBottom: 780,
      lastBottom: 690,
      gap: 10,
      overlap: 40,
      listBottomPadding: 80,
      offsetTop: 0,
      pageTop: 120,
      scrollY: 0,
      viewHeight: 800,
      windowHeight: 800,
    })
    expect(line).toContain('800ms')
    expect(line).toContain('C1')
    expect(line).toContain('C2')
    expect(line).toContain('C3')
    // P2：三个数都要出现在读数里
    expect(line).toContain('offsetTop 0')
    expect(line).toContain('pageTop 120')
    expect(line).toContain('scrollY 0')
  })
})

describe('installGoalCheck — 聚焦后 800ms、2000ms 各量一次', () => {
  let getInputBar
  let getList
  let getLastMessage
  let uninstall

  beforeEach(() => {
    vi.useFakeTimers()
    document.body.innerHTML = ''
    window.visualViewport = vv(0, 800)

    const root = document.createElement('div')
    const list = document.createElement('div')
    const last = document.createElement('div')
    const bar = document.createElement('div')
    const ta = document.createElement('textarea')
    bar.appendChild(ta)
    root.append(list, bar)
    document.body.appendChild(root)

    list.style.paddingBottom = '80px'
    list.getBoundingClientRect = () => rect(0, 690) // mock; not read directly
    last.getBoundingClientRect = () => rect(600, 690)
    bar.getBoundingClientRect = () => rect(700, 780)

    getInputBar = () => bar
    getList = () => list
    getLastMessage = () => last
    uninstall = installGoalCheck({ getInputBar, getList, getLastMessage })
  })

  afterEach(() => {
    uninstall?.()
    uninstall = null
    vi.useRealTimers()
    delete window.visualViewport
  })

  const overlay = () => document.querySelector('[data-kbd-goalcheck]')

  it('does not measure before any focus', () => {
    expect(overlay()).toBeNull()
  })

  it('measures at 800ms and again at 2000ms, showing both in the top overlay', () => {
    document.querySelector('textarea').focus()

    vi.advanceTimersByTime(800)
    expect(overlay().textContent).toContain('800ms')
    expect(overlay().textContent).not.toContain('2000ms')

    vi.advanceTimersByTime(1200)
    expect(overlay().textContent).toContain('2000ms')
  })

  it('a fresh focus clears the previous sample', () => {
    const ta = document.querySelector('textarea')
    ta.focus()
    vi.advanceTimersByTime(800)
    expect(overlay().textContent).toContain('800ms')

    ta.blur()
    ta.focus()
    vi.advanceTimersByTime(800)
    // exactly one sample block after the re-focus
    expect(overlay().textContent.split('800ms').length - 1).toBe(1)
  })

  it('ignores focus of a non-text element', () => {
    const btn = document.createElement('button')
    document.body.appendChild(btn)
    btn.focus()
    vi.advanceTimersByTime(MEASURE_AT_MS[MEASURE_AT_MS.length - 1])
    expect(overlay()).toBeNull()
  })

  it('cleanup removes the overlay and stops measuring', () => {
    const ta = document.querySelector('textarea')
    ta.focus()
    uninstall()
    uninstall = null
    vi.advanceTimersByTime(2000)
    expect(overlay()).toBeNull()
  })

  // P1：浮层是 position:fixed，会跟着 layout viewport 走；页面被键盘顶起时
  // 必须把 top 设成 visualViewport.offsetTop 才留在可视区内。
  it('P1: 浮层 top 跟着 visualViewport.offsetTop', () => {
    window.visualViewport.offsetTop = 120
    document.querySelector('textarea').focus()
    vi.advanceTimersByTime(800)
    expect(overlay().style.top).toBe('120px')
  })

  it('P1: visual viewport scroll（offsetTop 变化）后 top 更新', () => {
    document.querySelector('textarea').focus()
    vi.advanceTimersByTime(800)
    window.visualViewport.offsetTop = 260
    window.visualViewport.emit('scroll')
    expect(overlay().style.top).toBe('260px')
  })

  it('P1: visual viewport resize 后 top 更新', () => {
    document.querySelector('textarea').focus()
    vi.advanceTimersByTime(800)
    window.visualViewport.offsetTop = 180
    window.visualViewport.emit('resize')
    expect(overlay().style.top).toBe('180px')
  })

  it('P1: 卸载时摘掉 visualViewport 监听', () => {
    document.querySelector('textarea').focus()
    vi.advanceTimersByTime(800)
    // 装上了才谈得上摘（防「从没加过」误绿）
    const attached = Object.values(window.visualViewport._listeners).flat().length
    expect(attached).toBeGreaterThan(0)
    uninstall()
    uninstall = null
    const left = Object.values(window.visualViewport._listeners).flat().length
    expect(left).toBe(0)
  })

  // P2：读数里同时显示 offsetTop、pageTop、scrollY。
  it('P2: 读数显示 offsetTop / pageTop / scrollY', () => {
    window.visualViewport.offsetTop = 40
    Object.defineProperty(window, 'scrollY', { value: 55, configurable: true })
    const original = document.documentElement.getBoundingClientRect
    document.documentElement.getBoundingClientRect = () => rect(-120, 0)
    try {
      document.querySelector('textarea').focus()
      vi.advanceTimersByTime(800)
      const t = overlay().textContent
      expect(t).toContain('offsetTop 40')
      expect(t).toContain('pageTop 120')
      expect(t).toContain('scrollY 55')
    } finally {
      document.documentElement.getBoundingClientRect = original
      Object.defineProperty(window, 'scrollY', { value: 0, configurable: true })
    }
  })
})
