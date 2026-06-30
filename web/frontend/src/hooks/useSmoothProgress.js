import { useRef, useState, useEffect } from 'react'

const GAP = 6
const MAX_SOFT = 99
const DECAY_RATE = 0.08
const BASE_FRAME_MS = 16.67
const MIN_VISIBLE_DIFF = 0.1

export function nextPct(prev, softCap, dtMs, decay = DECAY_RATE) {
  if (prev >= 100) return 100
  if (softCap == null || softCap <= 0) return 0
  if (softCap <= prev) return prev
  const diff = softCap - prev
  const factor = 1 - Math.pow(1 - decay, dtMs / BASE_FRAME_MS)
  return prev + diff * factor
}

export default function useSmoothProgress(target, done) {
  const displayRef = useRef(0)
  const renderedRef = useRef(0)
  const targetRef = useRef(target)
  const lastTsRef = useRef(null)
  const rafRef = useRef(null)
  const [displayPct, setDisplayPct] = useState(0)

  useEffect(() => {
    if (done) {
      if (displayRef.current !== 100) {
        displayRef.current = 100
        renderedRef.current = 100
        setDisplayPct(100)
      }
      return
    }
    if (target != null && (targetRef.current == null || target > targetRef.current)) {
      targetRef.current = target
    }
  }, [target, done])

  useEffect(() => {
    if (done) return

    const loop = (timestamp) => {
      if (lastTsRef.current === null) {
        lastTsRef.current = timestamp
        rafRef.current = requestAnimationFrame(loop)
        return
      }
      const dtMs = Math.min(timestamp - lastTsRef.current, 500)
      lastTsRef.current = timestamp

      const curTarget = targetRef.current
      const softCap = curTarget != null
        ? Math.min(curTarget + GAP, MAX_SOFT)
        : null
      const prev = displayRef.current
      const next = nextPct(prev, softCap, dtMs)

      if (next > prev) {
        displayRef.current = next
        if (next - renderedRef.current >= MIN_VISIBLE_DIFF) {
          renderedRef.current = next
          setDisplayPct(next)
        }
      }

      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
      lastTsRef.current = null
    }
  }, [done])

  return displayPct
}
