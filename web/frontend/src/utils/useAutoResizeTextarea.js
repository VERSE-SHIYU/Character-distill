import { useCallback, useContext, useEffect, useRef } from 'react'
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
    if (manualH) {
      // 手动拖拽优先：高度严格等于 manualH，内容超出则内部滚动
      const h = Math.max(MIN_H, Math.min(manualH, HARD_MAX))
      ta.style.height = h + 'px'
      if (scrollH > h) ta.style.overflowY = 'auto'
    } else if (scrollH > AUTO_MAX) {
      // 无手动高、内容超自动上限：停在 AUTO_MAX 并滚动
      ta.style.height = AUTO_MAX + 'px'
      ta.style.overflowY = 'auto'
    } else {
      // 无手动高、内容未超限：随内容
      ta.style.height = Math.max(scrollH, MIN_H) + 'px'
    }
  }, [textareaRef, manualH])

  useEffect(() => { resize() }, [manualH, resize])

  return { textareaRef, resize }
}
