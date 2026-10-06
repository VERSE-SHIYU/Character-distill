import { useEffect } from 'react'
import { isTextEntry } from './textEntry'

/**
 * B5/B7/B8 — 聚焦文字输入框时的键盘配合。用事件委托监听根节点，
 * 不改任何输入框组件（ChatInputBar 不动）。
 *
 * B5：聚焦时给 <body> 加 kbd-focusing，失焦后下一个事件循环移除（换框不闪）。
 * B7：交给页面的 onFocus 回调（kbd-focusing 已加上之后再调）。
 * B8：非「iOS + Safari」时，聚焦后 300ms / 1000ms 各把输入栏滚进视野；
 *     iOS 非 Safari 失焦时页面滚回顶部；iOS 键盘收起但未失焦时让它失焦。
 */

// B8：照 ChatUI riseInput.js 的两次滚动时刻。
const RISE_AT_MS = [300, 1000]
const FOCUSING_CLASS = 'kbd-focusing'

/**
 * 由 UA 决定的滚动策略（照 ChatUI `utils/ua.js` + `riseInput.js`，去掉 iOS 12
 * 与 ArkWeb/AliApp 分支）。抽出来便于用三种 UA 直接测。
 */
export function keyboardRiseStrategy(ua) {
  const isIOS = /iPad|iPhone|iPod/.test(ua)
  const hasSafariToken = ua.includes('Safari/')
  return {
    isIOS,
    // 「iOS + Safari/」不处理；其余全部（含安卓、iOS 内嵌浏览器）都要滚
    scrollOnFocus: !(isIOS && hasSafariToken),
    scrollPageOnBlur: isIOS && !hasSafariToken,
    blurOnKbdClose: isIOS,
  }
}

export default function useKeyboardFocus({
  rootRef,
  enabled,
  onFocus,
  getRiseTarget,
  ua = navigator.userAgent,
}) {
  const { scrollOnFocus, scrollPageOnBlur, blurOnKbdClose } = keyboardRiseStrategy(ua)

  useEffect(() => {
    if (!enabled) return undefined
    const root = rootRef.current
    if (!root) return undefined

    let blurTimer = null
    let riseTimers = []
    let inputEl = null

    const clearRise = () => {
      riseTimers.forEach((t) => clearTimeout(t))
      riseTimers = []
    }

    const handleFocusIn = (e) => {
      if (!isTextEntry(e.target)) return
      clearTimeout(blurTimer) // 换框时不闪
      inputEl = e.target
      document.body.classList.add(FOCUSING_CLASS)
      // 顺序：类已在 body 上，页面回调里读 scrollHeight 才是新的留白高度
      onFocus?.(e.target)
      if (scrollOnFocus) {
        clearRise()
        riseTimers = RISE_AT_MS.map((ms) =>
          setTimeout(() => {
            if (!inputEl) return
            // P6：滚进视野的是页面给的输入区（由页面传 getRiseTarget），不是 input 本身
            ;(getRiseTarget?.(inputEl) ?? inputEl).scrollIntoView(false)
          }, ms),
        )
      }
    }

    const handleFocusOut = (e) => {
      if (!isTextEntry(e.target)) return
      blurTimer = setTimeout(() => {
        document.body.classList.remove(FOCUSING_CLASS)
        inputEl = null
        clearRise()
      }, 0)
      if (scrollPageOnBlur) {
        setTimeout(() => document.body.scrollIntoView())
      }
    }

    root.addEventListener('focusin', handleFocusIn)
    root.addEventListener('focusout', handleFocusOut)

    // D6/P7：iOS 键盘收起但输入框没失焦（可视视口长回原高）→ 让它失焦。
    // winHeight 只增不减：据 svedit PR #352 记录，iOS 键盘弹起瞬间 innerHeight
    // 会短暂变小，若每次现取 innerHeight 比较，这一瞬会被误判成「键盘收起」
    // 而把刚弹起的键盘收回。
    const vv = window.visualViewport
    let winHeight = window.innerHeight
    const handleVVResize = () => {
      if (window.innerHeight > winHeight) winHeight = window.innerHeight
      if (inputEl && vv.height >= winHeight) inputEl.blur()
    }
    if (blurOnKbdClose && vv) vv.addEventListener('resize', handleVVResize)

    return () => {
      root.removeEventListener('focusin', handleFocusIn)
      root.removeEventListener('focusout', handleFocusOut)
      if (blurOnKbdClose && vv) vv.removeEventListener('resize', handleVVResize)
      clearTimeout(blurTimer)
      clearRise()
      document.body.classList.remove(FOCUSING_CLASS)
    }
  }, [enabled, rootRef, onFocus, getRiseTarget, scrollOnFocus, scrollPageOnBlur, blurOnKbdClose])
}
