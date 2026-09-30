import { describe, it, expect } from 'vitest'
import { blockedBySameCharacter } from './groupMembers'

const cards = [
  { id: 'a1', name: '魏无羡' },
  { id: 'a2', name: '魏无羡' },
  { id: 'b1', name: '江澄' },
]

describe('blockedBySameCharacter', () => {
  it('同一角色已选了一个版本，另一个版本不能再选', () => {
    expect(blockedBySameCharacter(cards[1], ['a1'], cards)).toBe(true)
  })

  it('不同角色照常可选', () => {
    expect(blockedBySameCharacter(cards[2], ['a1'], cards)).toBe(false)
  })

  it('已选中的那张可以取消', () => {
    expect(blockedBySameCharacter(cards[0], ['a1'], cards)).toBe(false)
  })

  it('什么都没选时都可选', () => {
    expect(cards.every((c) => !blockedBySameCharacter(c, [], cards))).toBe(true)
  })
})
