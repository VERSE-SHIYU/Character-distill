import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import GroupPhasePicker from '../GroupPhasePicker'

// 纯展示组件：只测「choices → 行数/当前值」与「选下拉 → onChange(cardId, 阶段号)」。
// 打开下拉按 Enter（Radix 的 OPEN_KEYS 含 Enter）。

const CHOICES = [
  { cardId: 'a', card: { name: '孔乙己' }, value: 2, avatar: null,
    options: [{ value: '1', label: '阶段 1 · 冷' }, { value: '2', label: '阶段 2 · 热' }] },
  { cardId: 'b', card: { name: '阿Q' }, value: 1, avatar: null,
    options: [{ value: '1', label: '阶段 1 · 初' }, { value: '2', label: '阶段 2 · 终' }] },
]

describe('GroupPhasePicker', () => {
  it('每个 choice 一行，下拉显示各自的当前阶段', () => {
    render(<GroupPhasePicker choices={CHOICES} onChange={() => {}} />)
    const combos = screen.getAllByRole('combobox')
    expect(combos).toHaveLength(2)
    expect(screen.getByRole('combobox', { name: '孔乙己 的时期' }).textContent).toContain('阶段 2 · 热')
    expect(screen.getByRole('combobox', { name: '阿Q 的时期' }).textContent).toContain('阶段 1 · 初')
  })

  it('选一项 → onChange(cardId, 阶段号)', () => {
    const onChange = vi.fn()
    render(<GroupPhasePicker choices={CHOICES} onChange={onChange} />)
    const trigger = screen.getByRole('combobox', { name: '孔乙己 的时期' })
    fireEvent.keyDown(trigger, { key: 'Enter' })
    fireEvent.click(screen.getByRole('option', { name: '阶段 1 · 冷' }))
    expect(onChange).toHaveBeenCalledWith('a', 1)
  })
})
