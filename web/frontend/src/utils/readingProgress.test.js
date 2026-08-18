import { describe, it, expect } from 'vitest'
import { resolveSavedPage } from './readingProgress'

describe('resolveSavedPage', () => {
  it('prefers the exact scroll_position page', () => {
    expect(resolveSavedPage({ progress: 0.5, scroll_position: 42 }, 100)).toBe(42)
  })

  it('falls back to the fraction when scroll_position is absent', () => {
    expect(resolveSavedPage({ progress: 0.5 }, 100)).toBe(50)
  })

  it('fraction inverse matches the save formula (no off-by-one)', () => {
    // saved at page 50 of 100 → progress = 50/(100-1)
    expect(resolveSavedPage({ progress: 50 / 99 }, 100)).toBe(50)
  })

  it('clamps out-of-range pages', () => {
    expect(resolveSavedPage({ progress: 0, scroll_position: 999 }, 100)).toBe(99)
    expect(resolveSavedPage({ progress: 0 }, 100)).toBe(0)
  })
})
