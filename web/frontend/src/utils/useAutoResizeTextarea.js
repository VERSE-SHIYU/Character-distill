import { useCallback, useContext, useEffect, useRef } from 'react'
import { InputHeightContext, MIN_H, AUTO_MAX, HARD_MAX } from '../components/common/ResizableInputArea'

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
    if (manualH) {
      const h = Math.max(MIN_H, Math.min(manualH, HARD_MAX))
      ta.style.height = h + 'px'
      ta.style.overflowY = scrollH > h ? 'auto' : 'hidden'
    } else if (scrollH > AUTO_MAX) {
      ta.style.height = AUTO_MAX + 'px'
      ta.style.overflowY = 'auto'
    } else {
      ta.style.height = Math.max(scrollH, MIN_H) + 'px'
    }
  }, [textareaRef, manualH])

  useEffect(() => { resize() }, [manualH, resize])

  return { textareaRef, resize }
}
