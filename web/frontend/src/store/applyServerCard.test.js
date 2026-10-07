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

describe('写回服务端返回的卡', () => {
  it('updateCard：store 里是服务端的 selectable，不是本地提交的', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(
      reply({ ok: true, card: server({ selectable: true, phases: [] }) }))
    await useAppStore.getState().updateCard('c1', { name: 'x', character_arc: { selectable: false } })
    const s = useAppStore.getState()
    expect([JSON.parse(s.cards[0].card_json).character_arc.selectable,
      s.currentCard.card_json.character_arc.selectable]).toEqual([true, true])
  })

})
