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

  // 渲染门三类条目各自都算数：只剩一类时整块仍在（补充 14）。少认一类，那类卡的残留条目
  // 在界面上就够不着、挪不动 —— 数据没丢，但对用户等于丢了。
  it.each([
    ['只有做法', { behaviors: [{ situation: '被揭短', behavior: '涨红脸' }], overlay: {}, attitudes: [] }],
    ['只有字段值', { behaviors: [], overlay: { speaking_style: { tone: ['冷'] } }, attitudes: [] }],
    ['只有关系态度', { behaviors: [], overlay: {}, attitudes: [{ target: '掌柜', attitude: '怕', note: '', phase: 1 }] }],
  ])('只剩一类未定位条目（%s）：整块仍渲染，列出这一条', (_name, unlocated) => {
    const { container } = render(<UnlocatedList arc={{ phases: PHASES, unlocated }} onMove={vi.fn()} />)
    expect(container.querySelector('.card-unlocated')).toBeTruthy()
    expect(container.querySelectorAll('.card-unlocated .card-behavior-item')).toHaveLength(1)
  })

  it('默认阶段：态度用原来标的阶段，其余用最后阶段', () => {
    render(<UnlocatedList arc={arc} onMove={vi.fn()} />)
    // 全站 Select（Radix）：触发器显示所选项的 label
    const shown = screen.getAllByRole('combobox').map((s) => s.textContent)
    expect(shown).toEqual(['阶段 2 · 晚', '阶段 2 · 晚', '阶段 2 · 晚', '阶段 1 · 早'])
  })

  it('选阶段 → 点挪入 → onMove(分区, 序号, 阶段, 路径)', async () => {
    const onMove = vi.fn().mockResolvedValue()
    render(<UnlocatedList arc={arc} onMove={onMove} />)
    // 打开下拉走键盘 Enter（同 common/__tests__/Select.test.jsx），再点选项
    fireEvent.keyDown(screen.getAllByRole('combobox')[2], { key: 'Enter' })
    fireEvent.click(screen.getByRole('option', { name: '阶段 1 · 早' }))
    fireEvent.click(screen.getAllByRole('button', { name: '挪入' })[2])
    await waitFor(() => expect(onMove).toHaveBeenCalled())
    expect(onMove.mock.calls).toEqual([['overlay', 1, 1, 'speaking_style.tone']])
  })
})
