/**
 * 「待补对话示例」目标检查 · 编辑页（docs/specs/examples-pending.md §1）。
 *
 * 定好的目标（Shiyu 2026-10-10）：蒸馏出来的卡缺对话示例时弹出编辑页让用户自己填；填不出来，
 * 有一个按钮让系统重新找一次并填上；只能用一次；还是没有就不再提醒。
 *
 * 编辑页要守住的：有「重新找一次」的入口；找到的示例在编辑页里看得到，再点保存不会把它清掉；
 * 保存交回去的是打开时那张卡、那一版；保存或重新找没结束时关不掉。
 *
 * 走真实组件（EditCardModal）。宿主用一个带状态的小组件代替：「重新找」像真实的 store 一样，
 * 先把卡换成找过之后的，再返回结果。期望值都是字面量。
 *
 * 重新找一次
 * M1 传了 onRefindExamples → 有提示和「重新找一次」按钮；没传 → 没有
 * M2 找到了（示例在顶层）→「对话示例」栏里就是找到的；按钮没了；直接保存，写回的示例是找到的，
 *    带的是找过之后的版本号
 * M3 找到了（示例贴在阶段下）→ 编辑页里看得到这些示例；直接保存，阶段下的示例还在
 * M4 还是没找到 → 提示说明没找到、聊天不受影响；按钮没了
 * M5 调用出错 → 错误上屏，按钮还在，可以再点
 * M6 已经改了别的栏再点 → 先问一句；不同意就不找，改的内容还在；同意才找
 * 保存交回去的是打开时那张卡、那一版
 * M7 弹窗开着时宿主那边换了卡号、版本号和内容 → 保存交回去的卡号、版本号、未编辑的内容
 *    都是打开时的
 * M7b 同样的情况下点「重新找一次」→ 带去的也是打开时的卡号和版本号
 * 没结束时关不掉
 * M8 保存没结束时点「取消」或点遮罩 → 不关；保存失败后错误提示在
 * M9 重新找没结束时点「取消」→ 不关
 */
import { useState } from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'

if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

const PAIR = '路人：先前的话。\n角色：我说一句话。'
const EXAMPLES_PLACEHOLDER = /对方：xxx/
const REFIND = '重新找一次'

const PHASED = { name: '角色', identity: '原来的身份', character_arc: { axis: 'a', source_fingerprint: 'fp', phases: [
  { label: '早', state: '早年', start: 0, overlay: {} },
  { label: '晚', state: '晚年', start: 9, overlay: {} },
] } }

afterEach(() => vi.restoreAllMocks())

// 宿主：像角色页一样，卡从「store」来；重新找成功 → 先把卡换成找过之后的（不再待补），再返回。
function Host({ card, after, refind, onSave = async () => {}, onClose = () => {} }) {
  const [state, setState] = useState({ ...card, pending: true })
  const onRefindExamples = async (opened) => {
    const found = await refind(opened)
    setState({ ...after, pending: false })
    return found
  }
  return (
    <EditCardModal
      isOpen
      data={state.data}
      cardId={state.id}
      revision={state.revision}
      onSave={onSave}
      onClose={onClose}
      onRefindExamples={state.pending ? onRefindExamples : undefined}
    />
  )
}

const flat = (examples) => ({ name: '角色', identity: '原来的身份', dialogue_examples: examples })
const examplesBox = () => screen.getByPlaceholderText(EXAMPLES_PLACEHOLDER)

async function clickSave(onSave) {
  fireEvent.click(screen.getByText('保存'))
  await waitFor(() => expect(onSave).toHaveBeenCalled())
  return onSave.mock.calls.at(-1)
}

describe('重新找一次', () => {
  it('M1 传了 onRefindExamples 才有提示和按钮', () => {
    const { unmount } = render(
      <EditCardModal isOpen data={flat([])} cardId="c1" revision="r1" onSave={async () => {}} onClose={() => {}} />)
    expect(screen.queryByText(REFIND)).toBeNull()
    unmount()

    render(<EditCardModal isOpen data={flat([])} cardId="c1" revision="r1" onSave={async () => {}}
      onClose={() => {}} onRefindExamples={async () => true} />)
    expect(screen.getByText(REFIND)).toBeInTheDocument()
    expect(screen.getByText(/这张卡还没有对话示例/)).toBeInTheDocument()
  })

  it('M2 找到了（示例在顶层）→ 栏里就是找到的，保存不清掉，带新版本号', async () => {
    const refind = vi.fn().mockResolvedValue(true)
    const onSave = vi.fn().mockResolvedValue()
    render(<Host card={{ id: 'c1', revision: 'r1', data: flat([]) }}
      after={{ id: 'c1', revision: 'r2', data: flat([PAIR]) }} refind={refind} onSave={onSave} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(examplesBox().value).toBe(PAIR))
    expect(refind).toHaveBeenCalledWith({ cardId: 'c1', revision: 'r1' })
    expect(screen.queryByText(REFIND)).toBeNull()
    expect(screen.getByText(/找到了/)).toBeInTheDocument()
    const [saved, opened] = await clickSave(onSave)
    expect(saved.dialogue_examples).toEqual([PAIR])
    expect(opened).toEqual({ cardId: 'c1', revision: 'r2' })
  })

  it('M3 找到了（示例贴在阶段下）→ 编辑页里看得到，保存后还在', async () => {
    const found = { ...PHASED, character_arc: { ...PHASED.character_arc, phases: [
      { ...PHASED.character_arc.phases[0], overlay: { dialogue_examples: [PAIR] } },
      PHASED.character_arc.phases[1],
    ] } }
    const onSave = vi.fn().mockResolvedValue()
    render(<Host card={{ id: 'c1', revision: 'r1', data: PHASED }}
      after={{ id: 'c1', revision: 'r2', data: found }} refind={vi.fn().mockResolvedValue(true)} onSave={onSave} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(screen.getByText(/先前的话/)).toBeInTheDocument())
    expect(screen.getByText(/我说一句话/)).toBeInTheDocument()
    expect(examplesBox().value).toBe('')
    const [saved] = await clickSave(onSave)
    expect(saved.character_arc.phases[0].overlay).toEqual({ dialogue_examples: [PAIR] })
  })

  it('M4 还是没找到 → 说明没找到、聊天不受影响，按钮没了', async () => {
    render(<Host card={{ id: 'c1', revision: 'r1', data: flat([]) }}
      after={{ id: 'c1', revision: 'r1', data: flat([]) }} refind={vi.fn().mockResolvedValue(false)} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(screen.getByText(/还是没找到/)).toBeInTheDocument())
    expect(screen.getByText(/聊天不受影响/)).toBeInTheDocument()
    expect(screen.queryByText(REFIND)).toBeNull()
  })

  it('M5 调用出错 → 错误上屏，按钮还在，可以再点', async () => {
    const refind = vi.fn().mockRejectedValueOnce(new Error('上游接口限流，请稍后重试')).mockResolvedValue(true)
    render(<Host card={{ id: 'c1', revision: 'r1', data: flat([]) }}
      after={{ id: 'c1', revision: 'r2', data: flat([PAIR]) }} refind={refind} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(screen.getByText('上游接口限流，请稍后重试')).toBeInTheDocument())
    fireEvent.click(screen.getByText(REFIND))
    await waitFor(() => expect(examplesBox().value).toBe(PAIR))
    expect(refind).toHaveBeenCalledTimes(2)
  })

  it('M6 已经改了别的栏再点 → 先问；不同意就不找，改的还在；同意才找', async () => {
    const refind = vi.fn().mockResolvedValue(true)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    render(<Host card={{ id: 'c1', revision: 'r1', data: flat([]) }}
      after={{ id: 'c1', revision: 'r2', data: flat([PAIR]) }} refind={refind} />)
    fireEvent.change(screen.getByDisplayValue('原来的身份'), { target: { value: '改到一半' } })

    fireEvent.click(screen.getByText(REFIND))

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(refind).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('改到一半')).toBeInTheDocument()

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(examplesBox().value).toBe(PAIR))
    expect(refind).toHaveBeenCalledTimes(1)
  })

  it('M6b 什么都没改就点 → 不问，直接找', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<Host card={{ id: 'c1', revision: 'r1', data: flat([]) }}
      after={{ id: 'c1', revision: 'r2', data: flat([PAIR]) }} refind={vi.fn().mockResolvedValue(true)} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(examplesBox().value).toBe(PAIR))
    expect(confirm).not.toHaveBeenCalled()
  })
})

describe('保存交回去的是打开时那张卡、那一版', () => {
  it('M7 弹窗开着时宿主那边换了卡号、版本号和内容 → 交回去的都是打开时的', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const props = { isOpen: true, onSave, onClose: () => {} }
    const { rerender } = render(<EditCardModal {...props} cardId="甲" revision="甲-1"
      data={{ name: '甲', identity: '甲的身份', background: '甲的背景', awakening_message: '甲醒了' }} />)
    fireEvent.change(screen.getByDisplayValue('甲的身份'), { target: { value: '用户改的身份' } })
    rerender(<EditCardModal {...props} cardId="乙" revision="乙-7"
      data={{ name: '乙', identity: '乙的身份', background: '乙的背景', awakening_message: '乙醒了' }} />)

    const [saved, opened] = await clickSave(onSave)

    expect(opened).toEqual({ cardId: '甲', revision: '甲-1' })
    expect([saved.name, saved.identity, saved.background, saved.awakening_message])
      .toEqual(['甲', '用户改的身份', '甲的背景', '甲醒了'])
    expect(screen.getByText(/编辑角色卡 — 甲/)).toBeInTheDocument()
  })
})

describe('重新找带去的也是打开时那张卡、那一版', () => {
  it('M7b 弹窗开着时宿主那边版本号变了 → 重新找带的是打开时的', async () => {
    const refind = vi.fn(() => new Promise(() => {}))
    const props = { isOpen: true, onSave: async () => {}, onClose: () => {}, onRefindExamples: refind }
    const { rerender } = render(<EditCardModal {...props} cardId="c1" revision="r1" data={flat([])} />)
    rerender(<EditCardModal {...props} cardId="c1" revision="r2" data={flat([])} />)

    fireEvent.click(screen.getByText(REFIND))

    await waitFor(() => expect(refind).toHaveBeenCalledWith({ cardId: 'c1', revision: 'r1' }))
  })
})

describe('没结束时关不掉', () => {
  it('M8 保存没结束时点「取消」或点遮罩 → 不关；失败后错误提示在', async () => {
    let fail
    const onSave = vi.fn(() => new Promise((_, reject) => { fail = reject }))
    const onClose = vi.fn()
    render(<EditCardModal isOpen data={flat([])} cardId="c1" revision="r1" onSave={onSave} onClose={onClose} />)

    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    fireEvent.click(screen.getByText('取消'))
    fireEvent.click(document.querySelector('.modal-overlay'))

    expect(onClose).not.toHaveBeenCalled()
    fail(new Error('这张卡已在别处更新，请刷新后再改'))
    await waitFor(() => expect(screen.getByText('这张卡已在别处更新，请刷新后再改')).toBeInTheDocument())
    fireEvent.click(screen.getByText('取消'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('M9 重新找没结束时点「取消」→ 不关', async () => {
    const onClose = vi.fn()
    render(<EditCardModal isOpen data={flat([])} cardId="c1" revision="r1" onSave={async () => {}}
      onClose={onClose} onRefindExamples={() => new Promise(() => {})} />)

    fireEvent.click(screen.getByText(REFIND))
    await waitFor(() => expect(screen.getByText('正在找…')).toBeInTheDocument())
    fireEvent.click(screen.getByText('取消'))

    expect(onClose).not.toHaveBeenCalled()
  })
})
