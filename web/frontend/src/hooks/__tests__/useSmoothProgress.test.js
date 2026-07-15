import { describe, it, expect } from 'vitest'
import { nextPct } from '../useSmoothProgress'

// ============================================================
// useSmoothProgress — pure function tests
// ============================================================

describe('nextPct', () => {
  it('returns 100 when prev >= 100', () => {
    expect(nextPct(100, 50, 16.67)).toBe(100)
    expect(nextPct(100, null, 16.67)).toBe(100)
    expect(nextPct(200, 50, 16.67)).toBe(100)
  })

  it('returns 0 when softCap is null or <= 0', () => {
    expect(nextPct(0, null, 16.67)).toBe(0)
    expect(nextPct(50, 0, 16.67)).toBe(0)
    expect(nextPct(0, -1, 16.67)).toBe(0)
  })

  it('returns prev when softCap <= prev (no backward movement)', () => {
    expect(nextPct(50, 30, 16.67)).toBe(50)
    expect(nextPct(80, 80, 16.67)).toBe(80)
  })

  it('returns value in (prev, softCap) when softCap > prev', () => {
    const result = nextPct(10, 20, 16.67)
    expect(result).toBeGreaterThan(10)
    expect(result).toBeLessThan(20)
  })

  it('advances more with larger dtMs', () => {
    const r1 = nextPct(10, 50, 16.67)   // 1 frame
    const r2 = nextPct(10, 50, 100)     // ~6 frames worth
    expect(r2).toBeGreaterThan(r1)
  })

  it('approaches softCap asymptotically', () => {
    let prev = 0
    const softCap = 30
    for (let i = 0; i < 100; i++) {
      const next = nextPct(prev, softCap, 16.67)
      if (next <= prev) break // converged
      prev = next
    }
    expect(prev).toBeLessThanOrEqual(softCap)
    expect(prev).toBeGreaterThan(0)
  })
})

// ============================================================
// State machine — monotonic mode simulation
// (regression: the 21%→16% jitter reset bug)
// ============================================================

describe('useSmoothProgress monotonic mode (simulated)', () => {
  function simulateMonotonic(monotonic, targets) {
    const outputs = []
    let targetRef = null
    let displayRef = 0
    const GAP = 6
    const MAX_SOFT = 99
    const DT_MS = 100

    for (const t of targets) {
      if (monotonic) {
        // Monotonic: ignore smaller targets, only propagate larger
        if (targetRef == null || t > targetRef) {
          targetRef = t
        }
        // dropped targets silently ignored — no reset
      } else {
        // Default: target dropped → reset cycle
        if (targetRef != null && t < targetRef - 1) {
          displayRef = 0
        }
        if (targetRef == null || t > targetRef) {
          targetRef = t
        }
      }

      const softCap = targetRef != null ? Math.min(targetRef + GAP, MAX_SOFT) : null
      const next = nextPct(displayRef, softCap, DT_MS)
      if (next > displayRef) {
        displayRef = next
      }
      outputs.push(Math.round(displayRef * 10) / 10)
    }

    return outputs
  }

  // The exact jitter sequence that caused the 21%→16% production bug
  const jitterSequence = [5, 10, 15, 21, 16, 25, 40, 38, 55, 70, 90]

  it('monotonic=true: output is strictly non-decreasing despite jitter', () => {
    const result = simulateMonotonic(true, jitterSequence)
    for (let i = 1; i < result.length; i++) {
      expect(result[i]).toBeGreaterThanOrEqual(result[i - 1])
    }
    // Specifically verify jitter points don't cause regression
    expect(result[4]).toBeGreaterThanOrEqual(result[3]) // 21→16
    expect(result[7]).toBeGreaterThanOrEqual(result[6]) // 40→38
  })

  it('monotonic=false (default): jitter DOES cause reset (retry semantics preserved)', () => {
    const result = simulateMonotonic(false, jitterSequence)
    // At least one regression exists (the 21→16 jitter resets to 0)
    let hasRegression = false
    for (let i = 1; i < result.length; i++) {
      if (result[i] < result[i - 1]) {
        hasRegression = true
        break
      }
    }
    expect(hasRegression).toBe(true)
    // The largest regression should be severe (>10 point drop at reset)
    let maxDrop = 0
    for (let i = 1; i < result.length; i++) {
      maxDrop = Math.max(maxDrop, result[i - 1] - result[i])
    }
    expect(maxDrop).toBeGreaterThan(10)
  })

  it('monotonic=true handles steady ascent (no jitter)', () => {
    const result = simulateMonotonic(true, [0, 10, 20, 30, 40, 50, 100])
    for (let i = 1; i < result.length; i++) {
      expect(result[i]).toBeGreaterThanOrEqual(result[i - 1])
    }
  })
})

// ============================================================
// Soft cap & done behavior
// ============================================================

describe('useSmoothProgress boundary behavior (simulated)', () => {
  it('displayPct never exceeds MAX_SOFT (99) when not done', () => {
    let targetRef = 100 // target at 100
    let displayRef = 0
    const MAX_SOFT = 99
    const GAP = 6
    const DT_MS = 16.67

    for (let i = 0; i < 1000; i++) {
      const softCap = Math.min(targetRef + GAP, MAX_SOFT)
      displayRef = nextPct(displayRef, softCap, DT_MS)
    }
    // Should approach softCap but never exceed 99
    expect(displayRef).toBeLessThanOrEqual(MAX_SOFT)
    expect(displayRef).toBeGreaterThan(90)
  })
})
