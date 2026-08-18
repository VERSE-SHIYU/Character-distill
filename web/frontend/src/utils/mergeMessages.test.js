import { describe, it, expect } from 'vitest'
import { mergeMessages } from './mergeMessages'

describe('mergeMessages', () => {
  it('keeps optimistic temp messages not yet on the server', () => {
    const out = mergeMessages(
      [{ id: 'temp-1', _status: 'sending', content: 'hi', created_at: '2026-01-02T00:00:00Z' }],
      [{ id: 1, content: 'older', created_at: '2026-01-01T00:00:00Z' }]
    )
    expect(out.map((m) => m.id)).toEqual([1, 'temp-1'])
  })

  it('keeps older loaded pages when the poll returns only the newest page', () => {
    const out = mergeMessages(
      [
        { id: 5, created_at: '2026-01-01T00:00:00Z' },
        { id: 9, created_at: '2026-01-02T00:00:00Z' },
      ],
      [{ id: 10, created_at: '2026-01-03T00:00:00Z' }]
    )
    expect(out.map((m) => m.id)).toEqual([5, 9, 10])
  })

  it('prefers the local pending copy over the server copy for the same id', () => {
    const out = mergeMessages([{ id: 1, content: 'sent', _status: 'sending' }], [{ id: 1, content: 'server' }])
    expect(out).toHaveLength(1)
    expect(out[0].content).toBe('sent')
  })

  it('applies server refresh for a same-id message that is not pending', () => {
    const out = mergeMessages([{ id: 1, content: 'stale' }], [{ id: 1, content: 'fresh' }])
    expect(out[0].content).toBe('fresh')
  })

  it('dedupes by id across prev and server', () => {
    const out = mergeMessages([{ id: 1 }, { id: 2 }], [{ id: 2 }, { id: 3 }])
    expect(out.map((m) => m.id)).toEqual([1, 2, 3])
  })
})
