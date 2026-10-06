import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'
import { parseCardJson } from '../../utils/card'

// 编辑弹窗里三张对象列表（人物关系 / 弧线阶段 / 情境→行为）共用 ObjectListField。
// 这里守的是「保存时交出去的 card_json 形态」，不重复测 ObjectListField 自身的增删改。

const CARD = {
  name: '孙悟空',
  character_arc: {
    axis: '从桀骜到担当',
    phases: [{
      label: '桀骜不服',
      state: '大闹天宫前后，动辄动手',
      behaviors: [{ situation: '被人轻视', behavior: '当场动手', source_quote: '吃俺老孙一棒' }],
    }],
  },
  situation_behaviors: [{ situation: '被人当众质疑', behavior: '先反问对方凭什么', source_quote: '你凭什么这么说' }],
  relationships: [{ target: '唐僧', relation: '师父', attitude: '敬而不服', note: '他是我师父' }],
}

async function save(data) {
  const onSave = vi.fn(() => Promise.resolve())
  render(<EditCardModal isOpen data={parseCardJson({ card_json: data })} cardId="c1" onSave={onSave} onClose={() => {}} />)
  return {
    onSave,
    click: async () => {
      fireEvent.click(screen.getByText('保存'))
      await waitFor(() => expect(onSave).toHaveBeenCalled())
      return onSave.mock.calls[0][0]
    },
  }
}

describe('EditCardModal 对象列表', () => {
  it('不改直接保存：各张表（含阶段下的做法）原样交回，不带 _key，不在列里的键（note / source_quote）不丢', async () => {
    const { click } = await save(CARD)
    const saved = await click()
    expect(saved.character_arc).toEqual(CARD.character_arc)
    expect(saved.situation_behaviors).toEqual(CARD.situation_behaviors)
    expect(saved.relationships).toEqual(CARD.relationships)
  })

  it('改阶段名、改变化轴、改做法后保存：交出去的是改后的对象', async () => {
    const { click } = await save(CARD)
    fireEvent.change(screen.getByDisplayValue('桀骜不服'), { target: { value: '无法无天' } })
    fireEvent.change(screen.getByDisplayValue('从桀骜到担当'), { target: { value: ' 从无法无天到担当 ' } })
    fireEvent.change(screen.getByDisplayValue('先反问对方凭什么'), { target: { value: '抡棒就打' } })
    fireEvent.change(screen.getByDisplayValue('当场动手'), { target: { value: '先骂后打' } })
    const saved = await click()
    expect(saved.character_arc).toEqual({
      axis: '从无法无天到担当',
      phases: [{
        label: '无法无天',
        state: '大闹天宫前后，动辄动手',
        behaviors: [{ situation: '被人轻视', behavior: '先骂后打', source_quote: '吃俺老孙一棒' }],
      }],
    })
    expect(saved.situation_behaviors[0]).toEqual({ situation: '被人当众质疑', behavior: '抡棒就打', source_quote: '你凭什么这么说' })
  })

  it('旧卡（字符串数组弧线）：阶段可补填心态，保存为对象形态', async () => {
    const { click } = await save({ name: 'x', character_arc: ['起初冷漠'] })
    fireEvent.change(screen.getByPlaceholderText('心态/立场'), { target: { value: '冷漠' } })
    const saved = await click()
    expect(saved.character_arc).toEqual({ axis: '', phases: [{ label: '冷漠', state: '起初冷漠', behaviors: [] }] })
    expect(saved.situation_behaviors).toEqual([])
  })

  it('E16 阶段 overlay：不改直接保存时原样交回（不丢未知键）', async () => {
    const OVERLAY = {
      name: '孙悟空',
      character_arc: {
        axis: '',
        phases: [{
          label: '冷', state: '起初',
          overlay: { personality_traits: ['疑心重'], 'speaking_style.catchphrases': ['哼'] },
        }],
      },
    }
    const { click } = await save(OVERLAY)
    const saved = await click()
    expect(saved.character_arc.phases[0].overlay).toEqual({
      personality_traits: ['疑心重'], 'speaking_style.catchphrases': ['哼'],
    })
  })

  it('点了添加却没填的空行不保存', async () => {
    const { click } = await save(CARD)
    fireEvent.click(screen.getByText('+ 添加阶段做法'))
    fireEvent.click(screen.getByText('+ 添加阶段'))
    fireEvent.click(screen.getByText('+ 添加一条'))
    fireEvent.click(screen.getByText('+ 添加关系'))
    const saved = await click()
    expect(saved.character_arc.phases).toHaveLength(1)
    expect(saved.character_arc.phases[0].behaviors).toHaveLength(1)
    expect(saved.situation_behaviors).toHaveLength(1)
    expect(saved.relationships).toHaveLength(1)
  })
})
