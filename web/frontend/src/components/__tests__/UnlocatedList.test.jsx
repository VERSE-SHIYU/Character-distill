/**
 * spec arc-phase-unlocated §4 段 4：未定位区的渲染与挪动交互（选阶段 → 挪入 → onMove）。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import UnlocatedList from '../common/UnlocatedList'

const PHASES = [{ label: '早', state: 's1' }, { label: '晚', state: 's2' }]

describe('UnlocatedList 挪动交互', () => {
  const arc = {
    phases: PHASES,
    unlocated: {
      behaviors: [{ situation: '被揭短', behavior: '涨红脸' }],
      overlay: { speaking_style: { tone: ['冷', '热'] } },
      attitudes: [{ target: '掌柜', attitude: '怕', note: '', phase: 1 }],
    },
  }

  it('没有未定位条目：整块不渲染', () => {
    const { container } = render(<UnlocatedList arc={{ phases: PHASES, unlocated: {} }} onMove={vi.fn()} />)
    expect(container.innerHTML).toBe('')
  })

  it('默认阶段：态度用原来标的阶段，其余用最后阶段', () => {
    render(<UnlocatedList arc={arc} onMove={vi.fn()} />)
    const values = screen.getAllByRole('combobox').map((s) => s.value)
    expect(values).toEqual(['2', '2', '2', '1'])
  })

  it('选阶段 → 点挪入 → onMove(分区, 序号, 阶段, 路径)', async () => {
    const onMove = vi.fn().mockResolvedValue()
    render(<UnlocatedList arc={arc} onMove={onMove} />)
    const selects = screen.getAllByRole('combobox')
    fireEvent.change(selects[2], { target: { value: '1' } })
    fireEvent.click(screen.getAllByRole('button', { name: '挪入' })[2])
    await waitFor(() => expect(onMove).toHaveBeenCalled())
    expect(onMove.mock.calls).toEqual([['overlay', 1, 1, 'speaking_style.tone']])
  })
})
