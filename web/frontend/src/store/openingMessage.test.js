import { describe, it, expect } from 'vitest'
import { resolveOpeningMessages } from './openingMessage'

let _cid = 0
const withCid = (msg) => ({ ...msg, _cid: `t${++_cid}` })

describe('resolveOpeningMessages', () => {
  it('prioritises sessionLastMessage over backendFirstMessage', () => {
    const result = resolveOpeningMessages({
      sessionLastMessage: '欢迎回来！',
      backendFirstMessage: '你好！',
      cardFirstMessage: '嗨',
    }, withCid)
    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('欢迎回来！')
    expect(result[0].role).toBe('char')
    expect(result[0]._cid).toBeTruthy()
  })

  it('prioritises backendFirstMessage over cardFirstMessage', () => {
    const result = resolveOpeningMessages({
      backendFirstMessage: '我是后端生成的开场白',
      cardFirstMessage: '卡片默认开场白',
    }, withCid)
    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('我是后端生成的开场白')
  })

  it('uses cardFirstMessage as fallback when nothing else is available', () => {
    const result = resolveOpeningMessages({
      cardFirstMessage: '卡片兜底开场白',
    }, withCid)
    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('卡片兜底开场白')
  })

  it('returns empty array when all inputs are empty', () => {
    const result = resolveOpeningMessages({}, withCid)
    expect(result).toEqual([])
  })

  it('returns empty array when all inputs are empty strings', () => {
    const result = resolveOpeningMessages({
      sessionLastMessage: '',
      backendFirstMessage: '',
      cardFirstMessage: '',
    }, withCid)
    expect(result).toEqual([])
  })

  it('backend vs card conflict: only backend message is used', () => {
    const result = resolveOpeningMessages({
      backendFirstMessage: '后端开场白',
      cardFirstMessage: '卡片默认——不同内容',
    }, withCid)
    expect(result).toHaveLength(1)
    expect(result[0].content).toBe('后端开场白')
    expect(result[0].content).not.toBe('卡片默认——不同内容')
  })
})
