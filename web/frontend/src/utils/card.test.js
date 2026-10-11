import { describe, it, expect } from 'vitest'
import { parseCardJson, effectiveArcPhase, arcPhaseDisplay, arcPhaseOptions } from './card.js'

describe('parseCardJson identity normalization', () => {
  it('keeps string identity', () => {
    expect(parseCardJson({ card_json: { name: 'A', identity: '歌手' } }).identity).toBe('歌手')
  })

  it('keeps relationships object (ChatArea role presets)', () => {
    const c = parseCardJson({ card_json: { name: 'A', identity: { relationships: { 小明: '好友' } } } })
    expect(c.identity.relationships).toEqual({ 小明: '好友' })
  })

  it('coerces malformed {name, description} object to its name', () => {
    const c = parseCardJson({ card_json: { name: 'A', identity: { name: 'TestChar', description: 'x' } } })
    expect(c.identity).toBe('TestChar')
  })

  it('coerces object without name to its description', () => {
    const c = parseCardJson({ card_json: { identity: { description: '只有描述' } } })
    expect(c.identity).toBe('只有描述')
  })

  it('coerces empty object to empty string', () => {
    const c = parseCardJson({ card_json: { identity: {} } })
    expect(c.identity).toBe('')
  })

  it('does not mutate the shared card_json object', () => {
    const card = { card_json: { name: 'A', identity: { name: 'B', description: 'y' } } }
    parseCardJson(card)
    expect(card.card_json.identity).toEqual({ name: 'B', description: 'y' })
  })
})

describe('parseCardJson 弧线归一（旧卡字符串数组 → { axis, phases }）', () => {
  it('旧卡：字符串数组变成没有 label 的阶段', () => {
    const c = parseCardJson({ card_json: { name: 'A', character_arc: ['起初冷漠', '学会信任'] } })
    expect(c.character_arc).toEqual({
      axis: '',
      phases: [{ label: '', state: '起初冷漠' }, { label: '', state: '学会信任' }],
    })
  })

  it('新卡：规范形态原样返回，不拷贝', () => {
    const arc = { axis: '从桀骜到担当', phases: [{ label: '桀骜不服', state: '大闹天宫前后' }] }
    const cardJson = { name: 'A', character_arc: arc }
    const c = parseCardJson({ card_json: cardJson })
    expect(c).toBe(cardJson)
    expect(c.character_arc).toBe(arc)
  })

  it('没有弧线：保持缺失，不补空对象、不拷贝', () => {
    const cardJson = { name: 'A' }
    const c = parseCardJson({ card_json: cardJson })
    expect(c).toBe(cardJson)
    expect('character_arc' in c).toBe(false)
  })

  it('归一不改动传入的 card_json', () => {
    const card = { card_json: { character_arc: ['a'] } }
    parseCardJson(card)
    expect(card.card_json.character_arc).toEqual(['a'])
  })
})

describe('effectiveArcPhase（没选 = 最后阶段）', () => {
  const card = { card_json: { character_arc: { axis: 'x', phases: [{ label: '冷' }, { label: '' }, { label: '暖' }] } } }

  it('无阶段的卡 → null', () => {
    expect(effectiveArcPhase({ card_json: {} }, 1)).toBeNull()
    expect(effectiveArcPhase({ card_json: { character_arc: { phases: [] } } }, 2)).toBeNull()
  })

  it('k 为 null / 0 / 越界 → 最后阶段 n', () => {
    expect(effectiveArcPhase(card, null)).toBe(3)
    expect(effectiveArcPhase(card, 0)).toBe(3)
    expect(effectiveArcPhase(card, 4)).toBe(3)
    expect(effectiveArcPhase(card, -1)).toBe(3)
  })

  it('合法 k → k', () => {
    expect(effectiveArcPhase(card, 1)).toBe(1)
    expect(effectiveArcPhase(card, 2)).toBe(2)
  })
})

describe('arcPhaseDisplay（阶段文字，不看 selectable）', () => {
  const card = { card_json: { character_arc: { axis: 'x', phases: [{ label: '冷' }, { label: '' }, { label: '暖' }] } } }

  it('有 label：阶段 k/n · label', () => {
    expect(arcPhaseDisplay(card, 1)).toBe('阶段 1/3 · 冷')
  })

  it('无 label：只写阶段 k/n', () => {
    expect(arcPhaseDisplay(card, 2)).toBe('阶段 2/3')
  })

  it('没选 / 越界 → 最后阶段，显示它的 label', () => {
    expect(arcPhaseDisplay(card, null)).toBe('阶段 3/3 · 暖')
  })

  it('无阶段 → 空串', () => {
    expect(arcPhaseDisplay({ card_json: {} }, 1)).toBe('')
  })

  it('不看 selectable：不可选的卡照常显示', () => {
    const unselectable = { card_json: { character_arc: { phases: [{ label: '甲' }], selectable: false } } }
    expect(arcPhaseDisplay(unselectable, null)).toBe('阶段 1/1 · 甲')
  })
})

describe('arcPhaseOptions（下拉选项，value 为字符串）', () => {
  it('不可选 → []', () => {
    const card = { card_json: { character_arc: { phases: [{ label: '甲' }], selectable: false } } }
    expect(arcPhaseOptions(card)).toEqual([])
  })

  it('无阶段 → []', () => {
    expect(arcPhaseOptions({ card_json: {} })).toEqual([])
    expect(arcPhaseOptions({ card_json: { character_arc: { phases: [], selectable: true } } })).toEqual([])
  })

  it('可选：每阶段一项，value 为字符串，有 label 带 · label', () => {
    const card = { card_json: { character_arc: { phases: [{ label: '冷' }, { label: '' }], selectable: true } } }
    expect(arcPhaseOptions(card)).toEqual([
      { value: '1', label: '阶段 1 · 冷' },
      { value: '2', label: '阶段 2' },
    ])
  })
})
