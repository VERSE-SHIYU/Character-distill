/**
 * §1 goal check — pilot acceptance. Enabled by ?kbdcheck=1 (see keyboardMode.js).
 * After the input is focused it measures three criteria at 800ms and 2000ms and
 * paints the result at the top of the screen; a screenshot is the verdict.
 *
 * Elements are supplied by the page (getters) so this stays page-agnostic —
 * every later segment reuses it. Temporary: deleted when the migration ends.
 */

export const MEASURE_AT_MS = [800, 2000]

// C2 上界：列表底部留白 + 这 24px。阈值是初值，真机首跑后按需调整。
const C2_SLACK_PX = 24
// C3：最后一条消息至少这么多像素落在可视区内。
const C3_MIN_VISIBLE_PX = 20

/**
 * Pure judging: takes plain rects and a visualViewport snapshot, returns the
 * three verdicts plus the numbers worth showing. No DOM access here.
 *  视觉区 = [vv.offsetTop, vv.offsetTop + vv.height]
 */
export function evaluateGoalCheck({ inputRect, lastRect, listBottomPadding, vv, windowHeight }) {
  const viewTop = vv.offsetTop
  const viewBottom = vv.offsetTop + vv.height

  const c1 = inputRect.top >= viewTop && inputRect.bottom <= viewBottom

  const gap = inputRect.top - lastRect.bottom
  const c2 = gap >= 0 && gap <= listBottomPadding + C2_SLACK_PX

  const overlap = Math.max(
    0,
    Math.min(lastRect.bottom, viewBottom) - Math.max(lastRect.top, viewTop),
  )
  const c3 = overlap >= C3_MIN_VISIBLE_PX

  return {
    c1,
    c2,
    c3,
    inputTop: inputRect.top,
    inputBottom: inputRect.bottom,
    lastBottom: lastRect.bottom,
    gap,
    overlap,
    listBottomPadding,
    offsetTop: vv.offsetTop,
    viewHeight: vv.height,
    windowHeight,
  }
}

const mark = (v) => (v ? '通过' : '不通过')

export function formatReport(label, r) {
  return [
    `[${label}] C1 ${mark(r.c1)}  C2 ${mark(r.c2)}  C3 ${mark(r.c3)}`,
    `输入栏 ${r.inputTop.toFixed(0)}–${r.inputBottom.toFixed(0)}  末条底 ${r.lastBottom.toFixed(0)}  间距 ${r.gap.toFixed(0)}  留白 ${r.listBottomPadding}`,
    `offsetTop ${r.offsetTop.toFixed(0)}  pageTop ${r.pageTop.toFixed(0)}  scrollY ${r.scrollY.toFixed(0)}  可视高 ${r.viewHeight.toFixed(0)}  窗口高 ${r.windowHeight}`,
  ].join('\n')
}

const isTextEntry = (el) => {
  if (!el || !el.tagName) return false
  if (el.tagName === 'TEXTAREA') return true
  return el.tagName === 'INPUT' && (el.getAttribute('type') || 'text') === 'text'
}

/**
 * Install the check. Returns an uninstall function. `win`/`doc` are injectable
 * so tests can drive timers and a stubbed visualViewport.
 */
export function installGoalCheck({
  getInputBar,
  getList,
  getLastMessage,
  win = window,
  doc = document,
}) {
  let overlay = null

  const ensureOverlay = () => {
    if (!overlay) {
      overlay = doc.createElement('div')
      overlay.setAttribute('data-kbd-goalcheck', '')
      Object.assign(overlay.style, {
        position: 'fixed',
        top: '0px',
        left: '0',
        right: '0',
        zIndex: '2147483647',
        pointerEvents: 'none',
        background: 'rgba(0,0,0,.72)',
        color: '#0f0',
        font: '12px/1.45 ui-monospace, Menlo, monospace',
        padding: 'calc(4px + env(safe-area-inset-top)) 8px 4px',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      })
      doc.body.appendChild(overlay)
    }
    return overlay
  }

  // P1：浮层是 position:fixed，跟的是 layout viewport；页面被键盘顶起时它会被
  // 推出可视区（home screen 模式下还被灵动岛盖住）。每次写入把 top 顶到
  // visual viewport 顶端，scroll/resize 时再同步一次。
  const syncTop = () => {
    if (overlay && win.visualViewport) {
      overlay.style.top = `${win.visualViewport.offsetTop}px`
    }
  }

  const write = (text) => {
    const el = ensureOverlay()
    el.textContent = el.textContent ? `${el.textContent}\n${text}` : text
    syncTop()
  }

  let timers = []
  const clearTimers = () => {
    timers.forEach((t) => win.clearTimeout(t))
    timers = []
  }

  const measure = (label) => {
    const vvp = win.visualViewport
    if (!vvp) {
      write(`[${label}] 无 visualViewport，无法测量`)
      return
    }
    const barEl = getInputBar()
    const lastEl = getLastMessage()
    if (!barEl || !lastEl) {
      write(`[${label}] 找不到输入栏或最后一条消息`)
      return
    }
    const listEl = getList()
    const listBottomPadding = listEl
      ? (parseFloat(win.getComputedStyle(listEl).paddingBottom) || 0)
      : 0
    const report = evaluateGoalCheck({
      inputRect: barEl.getBoundingClientRect(),
      lastRect: lastEl.getBoundingClientRect(),
      listBottomPadding,
      vv: vvp,
      windowHeight: win.innerHeight,
    })
    // P2：页面顶起未必落在 offsetTop 上，也可能表现为文档滚动 / 根节点位移
    // （ChatUI 的 viewportTop 就是量根节点 rect.top）。三个数一起报才判断得了。
    report.pageTop = Math.abs(doc.documentElement.getBoundingClientRect().top)
    report.scrollY = win.scrollY
    write(formatReport(label, report))
  }

  const onFocusIn = (e) => {
    if (!isTextEntry(e.target)) return
    clearTimers()
    ensureOverlay().textContent = ''
    timers = MEASURE_AT_MS.map((ms) => win.setTimeout(() => measure(`${ms}ms`), ms))
  }

  doc.addEventListener('focusin', onFocusIn, true)

  const vvp = win.visualViewport
  if (vvp) {
    vvp.addEventListener('scroll', syncTop)
    vvp.addEventListener('resize', syncTop)
  }

  return () => {
    doc.removeEventListener('focusin', onFocusIn, true)
    vvp?.removeEventListener('scroll', syncTop)
    vvp?.removeEventListener('resize', syncTop)
    clearTimers()
    overlay?.remove()
    overlay = null
  }
}
