import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  MEASURE_AT_MS,
  evaluateGoalCheck,
  formatReport,
  installGoalCheck,
} from './goalCheck'

const rect = (top, bottom) => ({ top, bottom, height: bottom - top })
const vv = (offsetTop, height) => ({ offsetTop, height })

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

describe('evaluateGoalCheck — reported extras', () => {
  it('echoes the pushed-up distance, view height and window height', () => {
    const r = evaluateGoalCheck({
      inputRect: rect(300, 380),
      lastRect: rect(200, 290),
      listBottomPadding: 80,
      vv: vv(150, 600),
      windowHeight: 800,
    })
    expect(r.pushedUp).toBe(150)
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
      pushedUp: 0,
      viewHeight: 800,
      windowHeight: 800,
    })
    expect(line).toContain('800ms')
    expect(line).toContain('C1')
    expect(line).toContain('C2')
    expect(line).toContain('C3')
  })
})

describe('installGoalCheck — 聚焦后 800ms、2000ms 各量一次', () => {
  let getInputBar
  let getList
  let getLastMessage
  let uninstall
  let doc

  beforeEach(() => {
    vi.useFakeTimers()
    doc = document
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
})
