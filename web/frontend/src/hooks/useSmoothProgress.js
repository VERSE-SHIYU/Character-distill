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

function resetCycle(displayRef, renderedRef, targetRef, lastTsRef, target, setDisplayPct) {
  displayRef.current = 0
  renderedRef.current = 0
  targetRef.current = target
  lastTsRef.current = null
  setDisplayPct(0)
}

export default function useSmoothProgress(target, done, options = {}) {
  const { monotonic = false } = options
  const displayRef = useRef(0)
  const renderedRef = useRef(0)
  const targetRef = useRef(target)
  const lastTsRef = useRef(null)
  const rafRef = useRef(null)
  const prevDoneRef = useRef(done)
  const [displayPct, setDisplayPct] = useState(0)

  useEffect(() => {
    const prevDone = prevDoneRef.current
    prevDoneRef.current = done

    // done true→false transition → retry, reset everything
    if (!monotonic && prevDone && !done) {
      resetCycle(displayRef, renderedRef, targetRef, lastTsRef, target, setDisplayPct)
      return
    }

    if (done) {
      if (displayRef.current !== 100) {
        displayRef.current = 100
        renderedRef.current = 100
        setDisplayPct(100)
      }
      return
    }

    // target dropped significantly → new cycle with fresh progress
    if (!monotonic && target != null && targetRef.current != null && target < targetRef.current - 1) {
      resetCycle(displayRef, renderedRef, targetRef, lastTsRef, target, setDisplayPct)
      return
    }

    // Normal: propagate larger targets
    if (target != null && (targetRef.current == null || target > targetRef.current)) {
      targetRef.current = target
    }
  }, [target, done, monotonic])

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
