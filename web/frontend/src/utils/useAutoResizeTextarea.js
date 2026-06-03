import { useCallback, useRef } from 'react'

const MAX_HEIGHT = 120

export function useAutoResizeTextarea(externalRef) {
  const internalRef = useRef(null)
  const textareaRef = externalRef || internalRef

  const resize = useCallback(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, MAX_HEIGHT) + 'px'
  }, [textareaRef])

  return { textareaRef, resize }
}
