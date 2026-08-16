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
