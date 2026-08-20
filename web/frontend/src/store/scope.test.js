import { describe, it, expect, beforeEach, vi } from 'vitest'
import { scoped, bumpScope } from './scope'
import useAppStore from './useAppStore'
import { fetchWithTimeout, postJSON } from '../api/client'

vi.mock('../api/client', () => ({
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  fetchWithTimeout: vi.fn(),
  getToken: vi.fn(),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
}))

const affinityData = (label) => ({ affinity: label })
const res = (status, json) => ({ status, json: async () => json })
const gate = () => { let go; const p = new Promise((r) => { go = r }); return { p, go } }
const failGate = () => { let rej; const p = new Promise((_, r) => { rej = r }); return { p, reject: rej } }

beforeEach(() => {
  vi.resetAllMocks()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
  fetchWithTimeout.mockResolvedValue({ status: 204 }) // 默认：无数据
  postJSON.mockResolvedValue({})
  useAppStore.setState({
    sessionId: null,
    currentCard: null,
    currentTextId: null,
    affinity: null,
    voiceRefInfo: null,
    cards: [],
    identifiedChars: [],
    messages: [],
    sending: false,
    sessionUserRole: '',
    voiceEnabled: false,
    webSearchEnabled: false,
    agentMode: false,
    affinityEnabled: true,
  })
})

describe('scope（代际计数结构性竞态防护）', () => {
  it('0) 经历过 bumpScope 后发起的 action 仍能正常写入（at 在调用时捕获）', async () => {
    useAppStore.setState({ sessionId: 's1' }) // null→s1 触发 subscribe → bumpScope
    fetchWithTimeout.mockResolvedValueOnce(res(200, affinityData('new-data')))
    await useAppStore.getState().fetchAffinity()
    expect(useAppStore.getState().affinity?.affinity).toBe('new-data')
  })

  it('1) fetchAffinity 竞态：慢旧会话请求不覆盖新会话数据', async () => {
    const a = gate()
    const b = gate()
    fetchWithTimeout.mockImplementationOnce(() => a.p) // 会话 A 慢
    fetchWithTimeout.mockImplementationOnce(() => b.p) // 会话 B 快

    useAppStore.setState({ sessionId: 'A' })
    const pA = useAppStore.getState().fetchAffinity()
    useAppStore.setState({ sessionId: 'B' }) // 切换 → bump
    const pB = useAppStore.getState().fetchAffinity()

    b.go(res(200, affinityData('B-data')))
    await pB
    expect(useAppStore.getState().affinity?.affinity).toBe('B-data')

    a.go(res(200, affinityData('A-data')))
    await pA
    expect(useAppStore.getState().affinity?.affinity).toBe('B-data') // 不被 A 覆盖
  })

  it('2) loadVoiceRef 竞态：慢旧卡请求不覆盖新卡参考音频', async () => {
    const a = gate()
    const b = gate()
    fetchWithTimeout.mockImplementationOnce(() => a.p)
    fetchWithTimeout.mockImplementationOnce(() => b.p)

    useAppStore.setState({ currentCard: { id: 'cardA' } })
    const pA = useAppStore.getState().loadVoiceRef('cardA')
    useAppStore.setState({ currentCard: { id: 'cardB' } })
    const pB = useAppStore.getState().loadVoiceRef('cardB')

    b.go(res(200, { exists: true, ref_text: 'B-ref' }))
    await pB
    expect(useAppStore.getState().voiceRefInfo?.ref_text).toBe('B-ref')

    a.go(res(200, { exists: true, ref_text: 'A-ref' }))
    await pA
    expect(useAppStore.getState().voiceRefInfo?.ref_text).toBe('B-ref')
  })

  it('3) loadCards 竞态：慢旧文本请求不覆盖新文本卡片', async () => {
    const a = gate()
    const b = gate()
    fetchWithTimeout.mockImplementationOnce(() => a.p)
    fetchWithTimeout.mockImplementationOnce(() => b.p)

    useAppStore.setState({ currentTextId: 'tA' })
    const pA = useAppStore.getState().loadCards('tA')
    useAppStore.setState({ currentTextId: 'tB' })
    const pB = useAppStore.getState().loadCards('tB')

    b.go(res(200, [{ id: 'cardB' }]))
    await pB
    a.go(res(200, [{ id: 'cardA' }]))
    await pA
    expect(useAppStore.getState().cards).toEqual([{ id: 'cardB' }])
  })

  it('4) sendMessage 竞态：慢旧会话回复不追加进新会话消息', async () => {
    const a = gate()
    const b = gate()
    postJSON.mockImplementationOnce(() => a.p)
    postJSON.mockImplementationOnce(() => b.p)

    useAppStore.setState({ sessionId: 'A', messages: [] })
    const pA = useAppStore.getState().sendMessage('你好A')
    useAppStore.setState({ sessionId: 'B', messages: [] })
    const pB = useAppStore.getState().sendMessage('你好B')

    b.go({ user_msg_id: 'uB', user_created_at: 'tB', reply: '回复B', char_msg_id: 'cB', char_created_at: 'tB2' })
    await pB
    a.go({ user_msg_id: 'uA', user_created_at: 'tA', reply: '回复A', char_msg_id: 'cA', char_created_at: 'tA2' })
    await pA

    expect(useAppStore.getState().messages.map((m) => m.content)).toEqual(['你好B', '回复B'])
  })

  it('5) fetchAffinity catch：旧请求失败不清掉新会话数据', async () => {
    const a = failGate()
    const b = gate()
    fetchWithTimeout.mockImplementationOnce(() => a.p)
    fetchWithTimeout.mockImplementationOnce(() => b.p)

    useAppStore.setState({ sessionId: 'A' })
    const pA = useAppStore.getState().fetchAffinity()
    useAppStore.setState({ sessionId: 'B' })
    const pB = useAppStore.getState().fetchAffinity()

    b.go(res(200, affinityData('B-data')))
    await pB
    expect(useAppStore.getState().affinity?.affinity).toBe('B-data')

    a.reject(new Error('network')) // 旧请求失败
    await pA
    expect(useAppStore.getState().affinity?.affinity).toBe('B-data') // catch 的 null 不落地
  })

  it('6) 机制通用性：scoped 包装的 mock action 零手写守卫即自动免疫竞态', async () => {
    const writes = []
    const fakeSet = (patch) => writes.push(patch)
    // action 体内只有 setScoped，没有任何 `if (get().sessionId !== ...)` 守卫
    const mid = gate()
    const action = scoped(async (setScoped) => { await mid.p; setScoped({ v: 'stale' }) }, fakeSet, () => ({}))
    const p = action()
    bumpScope() // 身份变更发生在请求在途时
    mid.go()
    await p
    expect(writes).toEqual([]) // 写被自动丢弃
  })

  it('6b) 同一身份下 scoped 写正常落地', async () => {
    const writes = []
    const action = scoped(async (setScoped) => { setScoped({ v: 'ok' }) }, (p) => writes.push(p), () => ({}))
    await action()
    expect(writes).toEqual([{ v: 'ok' }])
  })
})
