import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'

// D24-A：编辑框里只读显示各阶段特有的内容。E1/E2/E5 改前必须红（it.fails）。
const DATA = {
  name: '小满',
  character_arc: {
    axis: '从冷漠到信任',
    phases: [
      {
        label: '冷漠',
        state: '故事开头',
        overlay: {
          personality_traits: ['疑心重'],
          speaking_style: { tone: '冷淡' },
          dialogue_examples: ['对方：你好\n角色：嗯。'],
          values: [],
        },
      },
      { label: '信任', state: '故事结尾' },
    ],
  },
}

function open() {
  const onSave = vi.fn().mockResolvedValue()
  render(<EditCardModal isOpen data={DATA} cardId="c1" onSave={onSave} onClose={() => {}} />)
  return onSave
}

describe('EditCardModal 阶段特有内容（只读）', () => {
  it('E1 打开编辑框：显示该阶段特有的性格与语气', () => {
    open()
    expect(screen.getByText('疑心重')).toBeTruthy()
    expect(screen.getByText('冷淡')).toBeTruthy()
    expect(screen.getByText('性格')).toBeTruthy()
    expect(screen.getAllByText('语气').length).toBeGreaterThanOrEqual(2)
  })

  it('E2 只读：疑心重不出现在任何输入框里', () => {
    open()
    expect(screen.getByText('疑心重')).toBeTruthy()
    const fields = Array.from(document.querySelectorAll('input, textarea'))
    expect(fields.some((el) => el.value.includes('疑心重'))).toBe(false)
  })

  it('E3 阶段的对话示例在页面上恰好出现一次（不和既有块重复）', () => {
    open()
    expect(screen.getAllByText((content) => content.includes('角色：嗯。'))).toHaveLength(1)
  })

  it('E4 阶段 2 没有 overlay：没有特有内容块，空列表不显示价值观', () => {
    open()
    expect(screen.queryByText(/第 2 阶段.*特有的内容/)).toBeNull()
    expect(screen.queryByText('价值观')).toBeNull()
  })

  it('E5 删掉阶段 1：疑心重随之消失', () => {
    open()
    expect(screen.getByText('疑心重')).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button', { name: '删除这一行' })[0])
    expect(screen.queryByText('疑心重')).toBeNull()
  })

  it('E6 不改直接保存：阶段 overlay 原样交回', async () => {
    const onSave = open()
    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][0].character_arc.phases[0].overlay).toEqual({
      personality_traits: ['疑心重'],
      speaking_style: { tone: '冷淡' },
      dialogue_examples: ['对方：你好\n角色：嗯。'],
      values: [],
    })
  })
})
