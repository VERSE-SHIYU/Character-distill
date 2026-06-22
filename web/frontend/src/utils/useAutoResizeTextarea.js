import { useCallback, useContext, useRef } from 'react'
import { InputHeightContext, MIN_H, HARD_MAX } from '../components/common/ResizableInputArea'

const AUTO_MAX = 160

export function useAutoResizeTextarea(externalRef) {
  const internalRef = useRef(null)
  const textareaRef = externalRef || internalRef
  const manualH = useContext(InputHeightContext)

  const resize = useCallback(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.overflowY = 'hidden'
    const scrollH = ta.scrollHeight
    const target = Math.min(Math.max(scrollH, manualH ?? 0), HARD_MAX)
    if (scrollH > AUTO_MAX && !manualH) {
      ta.style.height = AUTO_MAX + 'px'
      ta.style.overflowY = 'auto'
    } else {
      ta.style.height = Math.max(target, MIN_H) + 'px'
    }
  }, [textareaRef, manualH])

  return { textareaRef, resize }
}
