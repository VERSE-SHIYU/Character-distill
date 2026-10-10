/**
 * 编辑弹窗目标检查（docs/specs/edit-card-modal-form.md §1）。
 *
 * 定好的目标（方案第 6 项）：蒸馏出来的卡缺对话示例时，弹出编辑页让用户自己填。
 * 这一段守住它的两个前提：
 *   一、用户在编辑页填的示例，保存后还是一组一组的（对方一句 + 角色一句），不被拆散；
 *   二、编辑页每次打开，显示和保存的都是这张卡。
 *
 * 走真实组件（EditCardModal），只把 onSave 换成记录函数。期望值都是字面量。
 *
 * 一、示例按组保存
 * E1 卡上有两组示例，什么都不改直接保存 → 还是两组，每组两行
 * E2 卡上没有示例，用户手填两组（组间空行、带多余空格和空行）→ 两组，每组两行
 * E3 把示例清空 → 空列表
 * 二、每次打开都是这张卡
 * E4 同一个弹窗先开甲卡、关掉、换乙卡再开 → 显示和写回的都是乙卡的
 * E5 关掉后这张卡变了（比如补上了示例），再打开 → 显示和写回的都是变化后的
 * E6 改了没保存就取消，再打开 → 回到卡上的内容
 * 不多做（main 上就绿：回归守卫）
 * E7 弹窗开着时父组件重渲染、卡的内容没变 → 用户已经填的内容还在
 * E8 保存失败 → 弹窗不关，用户填的内容还在
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'

const PAIR1 = '掌柜：孔乙己么？你还欠十九个钱呢！\n孔乙己：这……下回还清罢。这一回是现钱，酒要好。'
const PAIR2 = '阿Q：秃儿！快回去，和尚等着你……\n小尼姑：你怎么动手动脚……'
const EXAMPLES_PLACEHOLDER = /对方：xxx/

function modal(props) {
  return <EditCardModal cardId="c" onClose={() => {}} {...props} />
}

async function clickSave(onSave) {
  fireEvent.click(screen.getByText('保存'))
  await waitFor(() => expect(onSave).toHaveBeenCalled())
  return onSave.mock.calls.at(-1)[0]
}

// ── 一、示例按组保存 ─────────────────────────────────────────────────────────

describe('一、示例按组保存', () => {
  it('E1 两组示例，不改直接保存 → 还是两组，每组两行', async () => {
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ isOpen: true, data: { name: 'x', dialogue_examples: [PAIR1, PAIR2] }, onSave }))

    const saved = await clickSave(onSave)

    expect(saved.dialogue_examples).toEqual([PAIR1, PAIR2])
  })

  it('E2 卡上没有示例，手填两组，组间空行、带多余空格和空行 → 两组，每组两行', async () => {
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ isOpen: true, data: { name: 'x' }, onSave }))
    const typed = '  掌柜：孔乙己么？你还欠十九个钱呢！  \n孔乙己：这……下回还清罢。这一回是现钱，酒要好。\n \n\n'
      + '阿Q：秃儿！快回去，和尚等着你……\n  小尼姑：你怎么动手动脚……\n\n'
    fireEvent.change(screen.getByPlaceholderText(EXAMPLES_PLACEHOLDER), { target: { value: typed } })

    const saved = await clickSave(onSave)

    expect(saved.dialogue_examples).toEqual([PAIR1, PAIR2])
  })

  it('E3 把示例清空 → 空列表', async () => {
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ isOpen: true, data: { name: 'x', dialogue_examples: [PAIR1] }, onSave }))
    fireEvent.change(screen.getByPlaceholderText(EXAMPLES_PLACEHOLDER), { target: { value: ' \n\n ' } })

    const saved = await clickSave(onSave)

    expect(saved.dialogue_examples).toEqual([])
  })
})

// ── 二、每次打开都是这张卡 ─────────────────────────────────────────────────────

describe('二、每次打开都是这张卡', () => {
  it('E4 同一个弹窗先开甲卡、关掉、换乙卡再开 → 显示和写回的都是乙卡的', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const a = { name: '甲', identity: '甲的身份', background: '甲的背景' }
    const b = { name: '乙', identity: '乙的身份', background: '乙的背景' }
    const { rerender } = render(modal({ isOpen: true, data: a, cardId: 'a', onSave }))
    rerender(modal({ isOpen: false, data: a, cardId: 'a', onSave }))
    rerender(modal({ isOpen: true, data: b, cardId: 'b', onSave }))

    expect(screen.getByDisplayValue('乙的身份')).toBeInTheDocument()
    const saved = await clickSave(onSave)

    expect([saved.name, saved.identity, saved.background]).toEqual(['乙', '乙的身份', '乙的背景'])
  })

  it('E5 关掉后这张卡变了，再打开 → 显示和写回的都是变化后的', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const v1 = { name: 'x', identity: '旧身份',
      character_arc: { axis: 'a', phases: [{ label: 'l', state: 's', overlay: { dialogue_examples: ['旧示例'] } }] } }
    const v2 = { name: 'x', identity: '新身份',
      character_arc: { axis: 'a', phases: [{ label: 'l', state: 's', overlay: { dialogue_examples: ['新示例'] } }] } }
    const { rerender } = render(modal({ isOpen: true, data: v1, onSave }))
    rerender(modal({ isOpen: false, data: v1, onSave }))
    rerender(modal({ isOpen: true, data: v2, onSave }))

    expect(screen.getByDisplayValue('新身份')).toBeInTheDocument()
    const saved = await clickSave(onSave)

    expect(saved.identity).toBe('新身份')
    expect(saved.character_arc.phases[0].overlay).toEqual({ dialogue_examples: ['新示例'] })
  })

  it('E6 改了没保存就取消，再打开 → 回到卡上的内容', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const card = { name: 'x', identity: '卡上的身份' }
    const { rerender } = render(modal({ isOpen: true, data: card, onSave }))
    fireEvent.change(screen.getByDisplayValue('卡上的身份'), { target: { value: '没保存的修改' } })
    rerender(modal({ isOpen: false, data: card, onSave }))
    rerender(modal({ isOpen: true, data: card, onSave }))

    expect(screen.getByDisplayValue('卡上的身份')).toBeInTheDocument()
    const saved = await clickSave(onSave)

    expect(saved.identity).toBe('卡上的身份')
  })
})

// ── 不多做（回归守卫）────────────────────────────────────────────────────────

describe('不多做', () => {
  it('E7 弹窗开着时父组件重渲染、卡的内容没变 → 用户已经填的内容还在', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const card = () => ({ name: 'x', identity: '卡上的身份',
      character_arc: { axis: 'a', phases: [{ label: 'l', state: 's' }] } })
    const { rerender } = render(modal({ isOpen: true, data: card(), onSave }))
    fireEvent.change(screen.getByDisplayValue('卡上的身份'), { target: { value: '填到一半' } })
    fireEvent.change(screen.getByDisplayValue('l'), { target: { value: '改过的阶段名' } })
    rerender(modal({ isOpen: true, data: card(), onSave }))

    expect(screen.getByDisplayValue('填到一半')).toBeInTheDocument()
    const saved = await clickSave(onSave)

    expect(saved.identity).toBe('填到一半')
    expect(saved.character_arc.phases[0].label).toBe('改过的阶段名')
  })

  it('E8 保存失败 → 弹窗不关，用户填的内容还在', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('这张卡已在别处更新，请刷新后再改'))
    render(modal({ isOpen: true, data: { name: 'x', identity: '卡上的身份' }, onSave }))
    fireEvent.change(screen.getByDisplayValue('卡上的身份'), { target: { value: '填到一半' } })

    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => expect(screen.getByText('这张卡已在别处更新，请刷新后再改')).toBeInTheDocument())
    expect(screen.getByDisplayValue('填到一半')).toBeInTheDocument()
  })
})
