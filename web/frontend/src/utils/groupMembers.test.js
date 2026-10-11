import { describe, it, expect } from 'vitest'
import { blockedBySameCharacter, aiMemberIds, parseCardIds } from './groupMembers'

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

describe('aiMemberIds（AI 成员 = 去掉扮演的那张卡）', () => {
  const ids = ['a', 'b', 'c']

  it('导演身份：全员都是 AI 成员', () => {
    expect(aiMemberIds(ids, 'director', '')).toEqual(['a', 'b', 'c'])
  })

  it('路人身份：全员都是 AI 成员', () => {
    expect(aiMemberIds(ids, 'stranger', '')).toEqual(['a', 'b', 'c'])
  })

  it('扮演身份：剔除扮演的卡，其余顺序不变', () => {
    expect(aiMemberIds(ids, 'character', 'b')).toEqual(['a', 'c'])
  })

  it('扮演身份但扮演的卡不在列表里：原样返回', () => {
    expect(aiMemberIds(ids, 'character', 'z')).toEqual(['a', 'b', 'c'])
  })
})

describe('parseCardIds', () => {
  it('数组原样返回', () => {
    expect(parseCardIds(['a', 'b'])).toEqual(['a', 'b'])
  })

  it('JSON 字符串解成数组', () => {
    expect(parseCardIds('["a","b"]')).toEqual(['a', 'b'])
  })

  it('坏字符串 / 空 → []', () => {
    expect(parseCardIds('not json')).toEqual([])
    expect(parseCardIds(undefined)).toEqual([])
    expect(parseCardIds('')).toEqual([])
  })
})
