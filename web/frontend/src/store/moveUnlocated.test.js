import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { fetchWithTimeout } from '../api/client'

// spec arc-phase-unlocated §6：编辑保存与挪动都写回**服务端返回**的卡（经 out_card，带现算的
// selectable），不写本地拼的那份 —— 否则 store 里留着过期的 selectable，开聊按钮读错。

vi.mock('../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
}))

const server = (arc) => ({ id: 'c1', card_json: JSON.stringify({ name: 'x', character_arc: arc }) })
const reply = (body, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => body })

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  const card = { id: 'c1', card_json: JSON.stringify({ name: 'x', character_arc: { selectable: false } }) }
  useAppStore.setState({ cards: [card], currentCard: { ...card }, sessionId: null })
})

describe('moveUnlocated（段 4）', () => {
  it('moveUnlocated：POST 到挪动接口，写回返回的卡', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(
      reply({ ok: true, card: server({ selectable: true, phases: [{ behaviors: [{ situation: 's' }] }] }) }))
    await useAppStore.getState().moveUnlocated('c1', { section: 'behaviors', index: 0, phase: 1, expected: { situation: 's' } })
    const [url, init] = vi.mocked(fetchWithTimeout).mock.calls[0]
    expect([url, JSON.parse(init.body),
      useAppStore.getState().currentCard.card_json.character_arc.phases[0].behaviors[0].situation])
      .toEqual(['/api/distill/card/c1/unlocated/move',
        { section: 'behaviors', index: 0, phase: 1, path: '', expected: { situation: 's' } }, 's'])
  })

  it('moveUnlocated 失败：抛错，本地卡不动', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ detail: '这一条已经不在未定位区，请刷新后重试' }, false))
    const before = useAppStore.getState().currentCard
    await expect(useAppStore.getState().moveUnlocated('c1', { section: 'behaviors', index: 9, phase: 1 }))
      .rejects.toThrow('请刷新后重试')
    expect(useAppStore.getState().currentCard).toBe(before)
  })
})
