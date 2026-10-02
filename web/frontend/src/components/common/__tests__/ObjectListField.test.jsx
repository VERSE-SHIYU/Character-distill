import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ObjectListField from '../ObjectListField'

const COLS = [
  { key: 'situation', placeholder: '情境', maxLength: 40 },
  { key: 'behavior', placeholder: '做法', maxLength: 80 },
]
const ROWS = [
  { situation: 'a', behavior: 'b', source_quote: 'q', _key: 1 },
  { situation: 'c', behavior: 'd', _key: 2 },
]

function setup(rows = ROWS, maxCount = 3) {
  const onChange = vi.fn()
  render(<ObjectListField legend="情境→行为" rows={rows} onChange={onChange} columns={COLS} maxCount={maxCount} addLabel="+ 添加一条" />)
  return onChange
}

describe('ObjectListField', () => {
  it('每行按列渲染输入框，带各列的 maxLength', () => {
    setup()
    const inputs = screen.getAllByPlaceholderText('做法')
    expect(inputs.map((el) => el.value)).toEqual(['b', 'd'])
    expect(inputs[0].maxLength).toBe(80)
    expect(screen.getAllByPlaceholderText('情境')[0].maxLength).toBe(40)
  })

  it('改一格：只改那一行那一列，行里其余键（含不在列里的）不动', () => {
    const onChange = setup()
    fireEvent.change(screen.getAllByPlaceholderText('做法')[0], { target: { value: 'B' } })
    expect(onChange).toHaveBeenCalledWith([
      { situation: 'a', behavior: 'B', source_quote: 'q', _key: 1 },
      ROWS[1],
    ])
  })

  it('删一行：删的是点的那一行', () => {
    const onChange = setup()
    fireEvent.click(screen.getAllByLabelText('删除这一行')[0])
    expect(onChange).toHaveBeenCalledWith([ROWS[1]])
  })

  it('添加：末尾多一行各列为空的新行', () => {
    const onChange = setup()
    fireEvent.click(screen.getByText('+ 添加一条'))
    const next = onChange.mock.calls[0][0]
    expect(next.slice(0, 2)).toEqual(ROWS)
    expect(next[2]).toMatchObject({ situation: '', behavior: '' })
    expect(next[2]._key).toBeTruthy()
  })

  it('到上限后不能再加；已有行照常显示', () => {
    setup(ROWS, 2)
    expect(screen.getByText('+ 添加一条').disabled).toBe(true)
    expect(screen.getAllByPlaceholderText('情境')).toHaveLength(2)
  })
})
