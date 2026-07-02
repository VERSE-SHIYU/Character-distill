import { useRef, useState, useCallback, useEffect, useMemo } from 'react'

export const CHAR_INTERVAL_MS = 20

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
  const isDoneRef = useRef(true)
  const [displayedText, setDisplayedText] = useState('')
  const [isDone, setIsDone] = useState(true)

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
        if (queueRef.current.length === 0 && !isDoneRef.current) {
          isDoneRef.current = true
          setIsDone(true)
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
    const wasEmpty = queueRef.current.length === 0
    queueRef.current = queueRef.current.concat([...text])
    if (wasEmpty) {
      lastTsRef.current = null
      accumRef.current = 0
    }
    if (isDoneRef.current) {
      isDoneRef.current = false
      setIsDone(false)
    }
  }, [])

  const flush = useCallback(() => {
    const remaining = queueRef.current
    if (remaining.length > 0) {
      displayedRef.current += remaining.join('')
      queueRef.current = []
      setDisplayedText(displayedRef.current)
    }
    if (!isDoneRef.current) {
      isDoneRef.current = true
      setIsDone(true)
    }
  }, [])

  const reset = useCallback(() => {
    queueRef.current = []
    displayedRef.current = ''
    accumRef.current = 0
    lastTsRef.current = null
    setDisplayedText('')
    isDoneRef.current = true
    setIsDone(true)
  }, [])

  return useMemo(() => ({ push, displayedText, flush, reset, isDone }), [push, displayedText, flush, reset, isDone])
}
