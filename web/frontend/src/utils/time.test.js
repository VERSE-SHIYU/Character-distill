process.env.TZ = 'Asia/Shanghai'
import { describe, it, expect } from 'vitest'
import { formatShortTime, formatDateTime } from './time'

describe('formatShortTime', () => {
  it('renders MM/DD HH:mm for a full ISO timestamp', () => {
    expect(formatShortTime('2026-08-17T14:30:00Z')).toBe('08/17 22:30')
  })

  it('handles space-separated naive timestamps (no T, no Z)', () => {
    expect(formatShortTime('2026-08-17 09:05:00')).toBe('08/17 17:05')
  })

  it('returns a dash for empty input and the raw value for garbage', () => {
    expect(formatShortTime('')).toBe('—')
    expect(formatShortTime('not-a-date')).toBe('not-a-date')
  })
})

describe('formatDateTime', () => {
  it('renders YYYY/MM/DD HH:mm for a full ISO timestamp', () => {
    expect(formatDateTime('2026-08-17T14:30:00Z')).toBe('2026/08/17 22:30')
  })

  it('includes the year (unlike formatShortTime)', () => {
    expect(formatDateTime('2025-12-31T00:00:00Z')).toBe('2025/12/31 08:00')
  })

  it('returns a dash for empty input', () => {
    expect(formatDateTime('')).toBe('—')
  })
})
