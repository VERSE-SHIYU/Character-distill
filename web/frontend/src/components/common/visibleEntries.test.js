import { describe, it, expect } from 'vitest'
import { visibleEntries } from './visibleEntries.js'

const entries = [
  { key: 'a', label: '总是可见' },
  { key: 'b', label: '要 isAdmin', requires: 'isAdmin' },
  { key: 'c', label: '要 canWrite', requires: 'canWrite' },
]

describe('visibleEntries', () => {
  it('无 requires 的条目一律可见', () => {
    expect(visibleEntries(entries, {}).map((e) => e.key)).toEqual(['a'])
  })

  it('flag 为 true 的条目露出', () => {
    expect(visibleEntries(entries, { isAdmin: true }).map((e) => e.key)).toEqual(['a', 'b'])
  })

  it('flag 为 false 的条目藏掉', () => {
    expect(visibleEntries(entries, { canWrite: false }).map((e) => e.key)).toEqual(['a'])
  })

  it('flag 缺失按藏掉处理（不是放行）', () => {
    expect(visibleEntries(entries, { isAdmin: true }).map((e) => e.key)).toEqual(['a', 'b'])
    expect(visibleEntries(entries, {}).map((e) => e.key)).toEqual(['a'])
  })

  it('多个 flag 同时给，各评各的', () => {
    expect(visibleEntries(entries, { isAdmin: true, canWrite: true }).map((e) => e.key)).toEqual(['a', 'b', 'c'])
  })

  it('保持原顺序，不重排', () => {
    const reordered = [entries[2], entries[1], entries[0]]
    expect(visibleEntries(reordered, { isAdmin: true, canWrite: true }).map((e) => e.key)).toEqual(['c', 'b', 'a'])
  })

  it('空 entries / 缺省 flags 不炸', () => {
    expect(visibleEntries([], {})).toEqual([])
    expect(visibleEntries(entries)).toEqual([entries[0]])
  })
})
