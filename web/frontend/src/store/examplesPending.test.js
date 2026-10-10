import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { fetchWithTimeout } from '../api/client'

// 「待补对话示例」在 store 里的三件事（docs/specs/examples-pending.md）：
// - 标记是服务端那一行的状态，store 以每次返回的行为准（保存、挪动、重新找都一样）；
// - 重新找：带版本号调接口，把返回的卡写回，告诉调用方找到没有；失败抛给调用方、store 不动；
// - 关掉：本地立刻清掉，再告诉服务端；没告诉成不抛。

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

const PAIR = '路人：先前的话。\n角色：我说一句话。'
const row = (over = {}) => ({ id: 'c1', revision: 'r2', examples_pending_for: null,
  card_json: JSON.stringify({ name: 'x', dialogue_examples: [PAIR] }), ...over })
const reply = (body, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => body })
const pending = () => {
  const s = useAppStore.getState()
  return [s.cards[0].examples_pending_for, s.currentCard.examples_pending_for]
}

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  const card = { id: 'c1', revision: 'r1', examples_pending_for: 'x', card_json: JSON.stringify({ name: 'x' }) }
  useAppStore.setState({ cards: [card, { id: 'c9', revision: 'z', examples_pending_for: 'y', card_json: '{"name":"y"}' }],
    currentCard: { ...card }, sessionId: null })
})

describe('待补对话示例', () => {
  it('重新找：带版本号调接口，写回返回的卡、版本号和标记，返回找到了', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ ok: true, found: true, card: row() }))

    const found = await useAppStore.getState().refindExamples('c1', 'r1')

    const [url, opts] = vi.mocked(fetchWithTimeout).mock.calls[0]
    expect([url, opts.method, JSON.parse(opts.body)]).toEqual(
      ['/api/distill/card/c1/examples/refind', 'POST', { revision: 'r1' }])
    expect(found).toBe(true)
    const s = useAppStore.getState()
    expect([s.cards[0].revision, s.currentCard.revision]).toEqual(['r2', 'r2'])
    expect([JSON.parse(s.cards[0].card_json).dialogue_examples, s.currentCard.card_json.dialogue_examples])
      .toEqual([[PAIR], [PAIR]])
    expect(pending()).toEqual([null, null])
    expect(s.cards[1].examples_pending_for).toBe('y')        // 别的卡不受影响
  })

  it('重新找：没找到 → 返回 false，标记照样按返回的行清掉', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ ok: true, found: false,
      card: row({ revision: 'r1', card_json: JSON.stringify({ name: 'x' }) }) }))

    expect(await useAppStore.getState().refindExamples('c1', 'r1')).toBe(false)
    expect(pending()).toEqual([null, null])
  })

  it('重新找失败：抛给调用方，store 不动（机会还在）', async () => {
    vi.mocked(fetchWithTimeout).mockRejectedValueOnce(new Error('上游接口限流，请稍后重试'))

    await expect(useAppStore.getState().refindExamples('c1', 'r1')).rejects.toThrow('上游接口限流')
    expect(pending()).toEqual(['x', 'x'])
    expect(useAppStore.getState().currentCard.revision).toBe('r1')
  })

  it('关掉：本地立刻清掉，再告诉服务端', async () => {
    let settle
    vi.mocked(fetchWithTimeout).mockReturnValueOnce(new Promise((resolve) => { settle = resolve }))

    const done = useAppStore.getState().dismissExamplesPending('c1')

    expect(pending()).toEqual([null, null])                    // 不等服务端
    const [url, opts] = vi.mocked(fetchWithTimeout).mock.calls[0]
    expect([url, opts.method]).toEqual(['/api/distill/card/c1/examples/dismiss', 'POST'])
    settle(reply({ ok: true, card: row({ revision: 'r1' }) }))
    await done
    expect(useAppStore.getState().cards[1].examples_pending_for).toBe('y')
  })

  it('关掉时没告诉成：不抛', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(fetchWithTimeout).mockRejectedValueOnce(new Error('network'))

    await expect(useAppStore.getState().dismissExamplesPending('c1')).resolves.toBeUndefined()
    warn.mockRestore()
  })

  it('保存：标记以返回的行为准（保存过就不再待补）', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ ok: true, card: row() }))

    await useAppStore.getState().updateCard('c1', { name: 'x' }, 'r1')

    expect(pending()).toEqual([null, null])
  })

  it('挪动：返回的行还是待补，store 里就还是待补', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce(reply({ ok: true, card: row({ examples_pending_for: 'x' }) }))

    await useAppStore.getState().moveUnlocated('c1', { section: 'behaviors', index: 0, phase: 1, revision: 'r1' })

    expect(pending()).toEqual(['x', 'x'])
  })
})
