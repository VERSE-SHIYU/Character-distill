import { describe, it, expect } from 'vitest'
import { withRowKeys, blankRow, cleanRows } from './objectRows.js'

const COLS = [{ key: 'situation' }, { key: 'behavior' }]

describe('objectRows', () => {
  it('withRowKeys：每行一个互不相同的 _key，原有键不动；空输入给空数组', () => {
    const rows = withRowKeys([{ situation: 'a', source_quote: 'q' }, { situation: 'b' }])
    expect(rows.map((r) => r.situation)).toEqual(['a', 'b'])
    expect(rows[0].source_quote).toBe('q')
    expect(new Set(rows.map((r) => r._key)).size).toBe(2)
    expect(withRowKeys(undefined)).toEqual([])
  })

  it('blankRow：各列空串，_key 不与已有行重复', () => {
    const [existing] = withRowKeys([{ situation: 'a' }])
    const row = blankRow(COLS)
    expect(row).toMatchObject({ situation: '', behavior: '' })
    expect(row._key).not.toBe(existing._key)
    expect(blankRow(COLS)._key).not.toBe(row._key)
  })

  it('cleanRows：去掉 _key，保留不在列里的键', () => {
    const rows = withRowKeys([{ situation: 'a', behavior: 'b', source_quote: 'q' }])
    expect(cleanRows(rows, COLS)).toEqual([{ situation: 'a', behavior: 'b', source_quote: 'q' }])
  })

  it('cleanRows：各列全空（含只有空白）的行丢掉，填了任一列的留下', () => {
    const rows = [
      { situation: '', behavior: '  ', _key: 1 },
      { situation: 'a', behavior: '', _key: 2 },
    ]
    expect(cleanRows(rows, COLS)).toEqual([{ situation: 'a', behavior: '' }])
  })
})
