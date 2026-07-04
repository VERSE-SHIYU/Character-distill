import { useRef, useEffect, useCallback } from 'react'

export function useAutoScroll(listRef, bottomRef, deps) {
  const userScrolledUp = useRef(false)

  useEffect(() => {
    const el = listRef.current
    if (!el) return
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (!userScrolledUp.current || isNearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, deps)

  const handleScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    userScrolledUp.current = el.scrollHeight - el.scrollTop - el.clientHeight > 80
  }, [listRef])

  // Scroll to bottom when keyboard opens (--vvh shrinks)
  useEffect(() => {
    const onVVChange = () => {
      const el = listRef.current
      if (!el) return
      el.scrollTop = el.scrollHeight
    }
    window.addEventListener('vvchange', onVVChange)
    return () => window.removeEventListener('vvchange', onVVChange)
  }, [listRef])

  return { handleScroll }
}
