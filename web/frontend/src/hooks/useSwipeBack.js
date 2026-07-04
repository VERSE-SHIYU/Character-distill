import { useRef, useCallback } from 'react'

/**
 * Left-edge swipe gesture to trigger a back action.
 * Returns touch event handlers to spread onto a page's root container.
 * Only activates when touch starts within 30px of the left edge.
 * Calls onBack when horizontal swipe exceeds 60px and dominates vertical motion.
 */
export default function useSwipeBack(onBack) {
  const ref = useRef({ startX: 0, startY: 0, activated: false })

  const onTouchStart = useCallback((e) => {
    if (e.touches.length !== 1) return
    const t = e.touches[0]
    ref.current = { startX: t.clientX, startY: t.clientY, activated: t.clientX < 30 }
  }, [])

  const onTouchMove = useCallback((e) => {
    if (!ref.current.activated) return
    const t = e.touches[0]
    const dx = t.clientX - ref.current.startX
    const dy = t.clientY - ref.current.startY
    if (dx > 60 && Math.abs(dx) > Math.abs(dy)) {
      ref.current.activated = false
      onBack?.()
    }
  }, [onBack])

  const onTouchEnd = useCallback(() => {
    ref.current.activated = false
  }, [])

  return { onTouchStart, onTouchMove, onTouchEnd }
}
