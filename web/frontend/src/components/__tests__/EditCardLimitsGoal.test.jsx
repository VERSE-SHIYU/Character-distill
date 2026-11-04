/**
 * 编辑框上限目标检查：上限只限制新输入（输入框 maxLength、列表的添加按钮），
 * 已有内容照常显示与保存，保存不检查上限。
 *
 * L1 34 条关系不改直接保存 → 保存成功、无 alert、34 条原样交回
 * L2 values 一行 45 字、personality_traits 10 条，只改名保存 → 保存成功、无 alert、原样交回
 * L3 已有 8 条关系 → 添加按钮 disabled（够了就不能再加）
 * L4 「核心价值观」输入框 maxLength = 180
 * L5 已有 34 条关系（超上限）→ 添加按钮 disabled（超了也不能再加）
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'

function modal(props) {
  return <EditCardModal cardId="c" isOpen onClose={() => {}} {...props} />
}

function relationships(n) {
  return Array.from({ length: n }, (_, i) => ({ target: `角色${i + 1}`, relation: '同伴', attitude: '友好' }))
}

async function clickSave(onSave) {
  fireEvent.click(screen.getByText('保存'))
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
  return onSave.mock.calls[0][0]
}

let alertSpy
afterEach(() => {
  alertSpy?.mockRestore()
  alertSpy = undefined
})

describe('上限只管新输入', () => {
  it.fails('L1 卡有 34 条关系，不改直接保存 → 保存成功、无 alert、34 条原样交回', async () => {
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ data: { name: 'x', relationships: relationships(34) }, onSave }))

    const saved = await clickSave(onSave)

    expect(alertSpy).not.toHaveBeenCalled()
    expect(saved.relationships).toHaveLength(34)
  })

  it.fails('L2 values 一行 45 字、personality_traits 10 条，只改名保存 → 保存成功、无 alert、原样交回', async () => {
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const onSave = vi.fn().mockResolvedValue()
    const values = ['正'.repeat(45)]
    const traits = Array.from({ length: 10 }, (_, i) => `特征${i + 1}`)
    render(modal({ editName: true, data: { name: '原名', values, personality_traits: traits }, onSave }))
    fireEvent.change(screen.getByDisplayValue('原名'), { target: { value: '乙' } })

    const saved = await clickSave(onSave)

    expect(alertSpy).not.toHaveBeenCalled()
    expect(saved.name).toBe('乙')
    expect(saved.values).toEqual(values)
    expect(saved.personality_traits).toEqual(traits)
  })
})

describe('添加按钮只管还能不能再加', () => {
  it('L3 已有 8 条关系 → 添加按钮 disabled', () => {
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ data: { name: 'x', relationships: relationships(8) }, onSave }))

    expect(screen.getByText('+ 添加关系')).toBeDisabled()
  })

  it('L5 已有 34 条关系（超上限）→ 添加按钮 disabled', () => {
    const onSave = vi.fn().mockResolvedValue()
    render(modal({ data: { name: 'x', relationships: relationships(34) }, onSave }))

    expect(screen.getByText('+ 添加关系')).toBeDisabled()
  })
})

it('L4 「核心价值观」输入框 maxLength = 180', () => {
  const onSave = vi.fn().mockResolvedValue()
  render(modal({ data: { name: 'x', values: ['忠于兄弟'] }, onSave }))

  expect(screen.getByDisplayValue('忠于兄弟').maxLength).toBe(180)
})
