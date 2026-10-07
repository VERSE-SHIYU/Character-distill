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

const server = (arc) => ({ id: 'c1', revision: 'r2', card_json: JSON.stringify({ name: 'x', character_arc: arc }) })
const reply = (body, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => body })

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  const card = { id: 'c1', revision: 'r1', card_json: JSON.stringify({ name: 'x', character_arc: { selectable: false } }) }
  useAppStore.setState({ cards: [card], currentCard: { ...card }, sessionId: null })
})

describe('写回服务端返回的卡', () => {
  it('updateCard：store 里是服务端的 selectable，不是本地提交的', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(
      reply({ ok: true, card: server({ selectable: true, phases: [] }) }))
    await useAppStore.getState().updateCard('c1', { name: 'x', character_arc: { selectable: false } }, 'r1')
    const s = useAppStore.getState()
    expect([JSON.parse(s.cards[0].card_json).character_arc.selectable,
      s.currentCard.card_json.character_arc.selectable]).toEqual([true, true])
  })

  // 乐观锁（后端 spec §13）：请求带编辑所依据的 revision；写回后 store 换成服务端的新 revision，
  // 下一次编辑按新的一版核对 —— 留着旧 revision 的话，连续两次保存第二次必 409。
  it('updateCard：请求带 revision，写回后 cards 与 currentCard 换成新 revision', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ ok: true, card: server({ phases: [] }) }))
    await useAppStore.getState().updateCard('c1', { name: 'x' }, 'r1')
    const s = useAppStore.getState()
    expect([JSON.parse(vi.mocked(fetchWithTimeout).mock.calls[0][1].body).revision,
      s.cards[0].revision, s.currentCard.revision]).toEqual(['r1', 'r2', 'r2'])
  })

  it('updateCard 失败（409）：抛给调用方，不写全局 error（弹窗自己呈现，不报两遍）', async () => {
    vi.mocked(fetchWithTimeout).mockRejectedValueOnce(new Error('这张卡已在别处更新，请刷新后再改'))
    useAppStore.setState({ error: null })
    await expect(useAppStore.getState().updateCard('c1', { name: 'x' }, 'r0')).rejects.toThrow('别处更新')
    expect([useAppStore.getState().error, useAppStore.getState().currentCard.revision]).toEqual([null, 'r1'])
  })

})
