import { useRef, useState, useCallback, useEffect } from 'react'

export const CHAR_INTERVAL_MS = 28

export function drainChars(queue, elapsedMs, intervalMs = CHAR_INTERVAL_MS) {
  const count = Math.floor(elapsedMs / intervalMs)
  const n = Math.min(count, queue.length)
  return {
    chars: queue.slice(0, n).join(''),
    remaining: queue.slice(n),
  }
}

export default function useTypewriter() {
  const queueRef = useRef([])
  const displayedRef = useRef('')
  const lastTsRef = useRef(null)
  const accumRef = useRef(0)
  const rafRef = useRef(null)
  const [displayedText, setDisplayedText] = useState('')

  useEffect(() => {
    const loop = (timestamp) => {
      if (lastTsRef.current === null) {
        lastTsRef.current = timestamp
        rafRef.current = requestAnimationFrame(loop)
        return
      }

      const dtMs = Math.min(timestamp - lastTsRef.current, 500)
      lastTsRef.current = timestamp

      const queue = queueRef.current
      if (queue.length > 0) {
        accumRef.current += dtMs
        const count = Math.floor(accumRef.current / CHAR_INTERVAL_MS)
        if (count > 0) {
          const n = Math.min(count, queue.length)
          const chars = queue.slice(0, n).join('')
          queueRef.current = queue.slice(n)
          displayedRef.current += chars
          setDisplayedText(displayedRef.current)
          accumRef.current -= count * CHAR_INTERVAL_MS
        }
      }

      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  const push = useCallback((text) => {
    if (!text) return
    queueRef.current = queueRef.current.concat([...text])
  }, [])

  const flush = useCallback(() => {
    const remaining = queueRef.current
    if (remaining.length > 0) {
      displayedRef.current += remaining.join('')
      queueRef.current = []
      setDisplayedText(displayedRef.current)
    }
  }, [])

  const reset = useCallback(() => {
    queueRef.current = []
    displayedRef.current = ''
    accumRef.current = 0
    lastTsRef.current = null
    setDisplayedText('')
  }, [])

  return { push, displayedText, flush, reset }
}
