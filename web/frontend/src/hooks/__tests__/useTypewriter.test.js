import { describe, it, expect } from 'vitest'
import { drainChars, CHAR_INTERVAL_MS } from '../useTypewriter'

// ============================================================
// drainChars — pure function tests
// ============================================================

describe('drainChars', () => {
  it('returns empty result for empty queue', () => {
    const result = drainChars([], 100, 28)
    expect(result.chars).toBe('')
    expect(result.remaining).toEqual([])
  })

  it('drains 0 chars when elapsedMs < intervalMs', () => {
    const queue = ['a', 'b', 'c']
    const result = drainChars(queue, 10, 100)
    expect(result.chars).toBe('')
    expect(result.remaining).toEqual(['a', 'b', 'c'])
  })

  it('drains correct number of chars based on elapsed/interval', () => {
    const queue = ['a', 'b', 'c', 'd', 'e']
    // elapsedMs=100, intervalMs=28 => floor(100/28)=3
    const result = drainChars(queue, 100, 28)
    expect(result.chars).toBe('abc')
    expect(result.remaining).toEqual(['d', 'e'])
  })

  it('preserves remainder across consecutive calls', () => {
    let queue = ['a', 'b', 'c', 'd', 'e']
    // Call 1: drain 3 chars (elapsed 100ms)
    let r1 = drainChars(queue, 100, 28)
    expect(r1.chars).toBe('abc')
    // Call 2: drain 2 from remaining (elapsed 60ms)
    let r2 = drainChars(r1.remaining, 60, 28)
    expect(r2.chars).toBe('de')
    expect(r2.remaining).toEqual([])
  })

  it('does not drain more than queue length', () => {
    const queue = ['a', 'b']
    const result = drainChars(queue, 10000, 1) // would ask for 10000
    expect(result.chars).toBe('ab')
    expect(result.remaining).toEqual([])
  })
})

// ============================================================
// Emoji / code-point safety (regression: emoji corruption bug)
// ============================================================

describe('drainChars — emoji / code-point safety', () => {
  // The emoji string contains multi-byte characters:
  // "你" (U+4F60), "好" (U+597D), 👨‍👩‍👧 (ZWJ sequence: 5 code points), 😀 (U+1F600)
  const emojiStr = '你好👨‍👩‍👧😀'
  const codePoints = [...emojiStr]

  it('[...str] splits by Unicode code point, preserving multi-codepoint emoji', () => {
    // 👨‍👩‍👧 is a ZWJ sequence of 5 codepoints: 👨 + ZWJ + 👩 + ZWJ + 👧
    // So total = 2 (你好) + 5 (👨‍👩‍👧) + 1 (😀) = 8 code points
    expect(codePoints.length).toBe(8)
    // First two are Chinese characters
    expect(codePoints[0]).toBe('你')
    expect(codePoints[1]).toBe('好')
    // Check ZWJ sequence is preserved codepoint-by-codepoint
    expect(codePoints[2]).toBe('👨')
    expect(codePoints[3]).toBe('‍') // ZWJ
  })

  it('draining code points and rejoining preserves original string', () => {
    let queue = [...emojiStr]
    const collected = []

    // Drain one code-point at a time
    for (let i = 0; i < codePoints.length; i++) {
      const result = drainChars(queue, 50, 28) // 50ms/28ms = 1 char per call
      collected.push(result.chars)
      queue = result.remaining
    }

    // Verify no broken surrogates in intermediate states
    for (const part of collected) {
      if (part === '') continue
      // No � (U+FFFD) replacement characters
      expect(part).not.toContain('�')
      // Each part should be valid — no unmatched surrogates
      for (const char of part) {
        const code = char.codePointAt(0)
        // Not a lone surrogate (0xD800-0xDFFF are surrogates)
        if (code >= 0xD800 && code <= 0xDFFF) {
          throw new Error(`Lone surrogate found: U+${code.toString(16)}`)
        }
      }
    }

    // Rejoin should equal original
    expect(collected.join('')).toBe(emojiStr)
  })

  it('queue built with [...text] drains back to exact original string', () => {
    const testStrings = [
      'Hello World',
      '你好世界',
      '👨‍👩‍👧',
      'abc👨‍👩‍👧def😀xyz',
      'áé', // combining characters
      'x\xF0\x9F\x98\x80y', // just to be safe — raw bytes not valid JS
    ]

    for (const original of testStrings) {
      const queue = [...original]
      const drainedParts = []
      let remaining = queue

      while (remaining.length > 0) {
        const result = drainChars(remaining, 40, 28) // 1 char per call
        drainedParts.push(result.chars)
        remaining = result.remaining
      }

      expect(drainedParts.join('')).toBe(original)
    }
  })

  it('default CHAR_INTERVAL_MS is 40', () => {
    expect(CHAR_INTERVAL_MS).toBe(40)
  })
})
