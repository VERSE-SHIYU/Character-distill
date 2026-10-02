import { describe, it, expect } from 'vitest'
import { parseCardJson } from './card.js'

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

  it('阶段里混着字符串与对象（编辑保存前的过渡形态）也归一', () => {
    const c = parseCardJson({ card_json: { character_arc: { axis: 'x', phases: ['旧', { label: 'L' }] } } })
    expect(c.character_arc).toEqual({ axis: 'x', phases: [{ label: '', state: '旧' }, { label: 'L', state: '' }] })
  })

  it('归一不改动传入的 card_json', () => {
    const card = { card_json: { character_arc: ['a'] } }
    parseCardJson(card)
    expect(card.card_json.character_arc).toEqual(['a'])
  })
})
