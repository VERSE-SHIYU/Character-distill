import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import useAppStore from './useAppStore'

// 回复还在生成时切走再回来，输入框不能被锁死、回复也不能落到别人的气泡里。
//
// 只桩 `streamSSE` 与 `postJSON`（其余照真实现）。桩的 cancel 与真实现同形：abort 之后
// **异步**走 onError（`api/client.js:278-283` 的 AbortError 分支），已经收过尾就不再回调。
// 「先同步交出所有权、再 abort」这条修法只有在收尾帧迟到时才看得出对错 —— 桩若同步回调，
// 两种写法都绿，等于没测。

const h = vi.hoisted(() => {
  const calls = []
  const postCalls = []
  let postImpl = async () => ({})
  class MockAppError extends Error {
    constructor(message, status = 0, code = '') {
      super(message)
      this.name = 'AppError'
      this.status = status
      this.code = code
    }
  }
  const streamSSE = (url, body, onToken, onDone, onError) => {
    const call = { url, body, aborted: false, finished: false }
    call.emitToken = (t) => onToken(t)
    call.emitDone = (p) => { call.finished = true; onDone(p) }
    call.emitError = (e, p) => { call.finished = true; onError(e, p) }
    call.cancel = () => {
      if (call.aborted) return
      call.aborted = true
      queueMicrotask(() => { if (!call.finished) onError(new MockAppError('请求超时，请重试', 408)) })
    }
    calls.push(call)
    return call.cancel
  }
  const postJSON = (...args) => { postCalls.push(args); return postImpl(...args) }
  return {
    calls,
    postCalls,
    streamSSE,
    postJSON,
    reset: () => { calls.length = 0; postCalls.length = 0; postImpl = async () => ({}) },
    setPostImpl: (fn) => { postImpl = fn },
  }
})

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    streamSSE: h.streamSSE,
    postJSON: h.postJSON,
    fetchWithTimeout: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  }
})

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const serverHistory = async () => ({
  session: { id: 's1', card_id: 'c1', character_name: '甲', text_id: 't1' },
  messages: [{ role: 'user', content: '你好', id: 1, created_at: '2026-09-29T00:00:00Z' }],
})

const startStream = (message = '你好') => {
  useAppStore.getState().sendMessageStream(message)
  return h.calls[h.calls.length - 1]
}

const lastCid = () => {
  const msgs = useAppStore.getState().messages
  return msgs[msgs.length - 1]._cid
}

beforeEach(() => {
  h.reset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  useAppStore.setState({
    sessionId: 's1',
    currentCard: { id: 'c1', name: '甲' },
    messages: [],
    sending: false,
    error: null,
    currentView: 'chat',
    resumeLoading: false,
    _pendingChatCardId: null,
    _chatStream: null,
    sessionUserRole: '',
    voiceEnabled: false,
    webSearchEnabled: false,
    agentMode: false,
    affinityEnabled: false,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('回复在途时切走再回来', () => {
  it('resumeSession 换到别的会话：旧流被放下，发送中立即解锁', async () => {
    const a = startStream()
    expect(useAppStore.getState().sending).toBe(true)

    h.setPostImpl(async () => ({
      session: { id: 's2', card_id: 'c2', character_name: '乙', text_id: 't1' },
      messages: [{ role: 'char', content: '那边的历史', id: 9, created_at: 'x' }],
    }))
    await useAppStore.getState().resumeSession('s2')
    await flush()

    expect(a.aborted, '旧流没被 abort').toBe(true)
    expect(useAppStore.getState().sending, '发送中没解锁，输入框会卡住').toBe(false)
    expect(useAppStore.getState().sessionId).toBe('s2')
    expect(useAppStore.getState()._chatStream).toBeNull()
  })

  it('selectCard 换到别的角色：解锁', async () => {
    const a = startStream()

    await useAppStore.getState().selectCard({ id: 'c2', session_id: 's2', name: '乙' })
    await flush()

    expect(a.aborted).toBe(true)
    expect(useAppStore.getState().sending, '发送中没解锁，输入框会卡住').toBe(false)
    expect(useAppStore.getState().sessionId).toBe('s2')
  })

  it('selectCard 点回同一张卡：不新开流，回复仍落回原会话并解锁（回归）', async () => {
    const a = startStream()

    await useAppStore.getState().selectCard({ id: 'c1', session_id: 's1', name: '甲' })
    await flush()

    expect(h.calls.length, '同一张卡不该再开一条流').toBe(1)
    expect(a.aborted, '同一张卡不该放下在途流').toBe(false)

    a.emitToken('回')
    a.emitDone({ char_msg_id: 7 })
    await flush()

    const msgs = useAppStore.getState().messages
    expect(msgs[msgs.length - 1].role).toBe('char')
    expect(msgs[msgs.length - 1].id).toBe(7)
    expect(msgs[msgs.length - 1].content).toBe('回')
    expect(useAppStore.getState().sending).toBe(false)
  })

  it('resumeSession 回到正在生成回复的同一会话：不重载，流继续写自己的气泡', async () => {
    const a = startStream('你好')
    const cid = useAppStore.getState().messages[1]._cid   // 角色气泡
    a.emitToken('前半')

    h.setPostImpl(serverHistory)
    await useAppStore.getState().resumeSession('s1')
    await flush()

    a.emitToken('后半')
    await flush()

    const msgs = useAppStore.getState().messages
    const bubble = msgs.find((m) => m._cid === cid)
    expect(bubble, '服务端那份历史（回复还没落库）把在途气泡挤掉了').toBeTruthy()
    expect(bubble.content).toBe('前半后半')
    expect(h.postCalls.length, '同一会话不该重载').toBe(0)
    expect(useAppStore.getState().sending).toBe(true)
  })

  it('被放下的旧流迟到的 done 帧：不解锁新流，也不改新会话的消息', async () => {
    const a = startStream('第一句')

    await useAppStore.getState().selectCard({ id: 'c2', session_id: 's2', name: '乙' })
    await flush()
    const b = startStream('第二句')
    const bCid = lastCid()
    b.emitToken('乙的回复')
    await flush()
    expect(useAppStore.getState().sending).toBe(true)

    a.emitDone({ char_msg_id: 99 })
    await flush()

    expect(useAppStore.getState().sending, '迟到帧把新流误解锁了').toBe(true)
    const bBubble = useAppStore.getState().messages.find((m) => m._cid === bCid)
    expect(bBubble.id, '迟到帧改了新会话的气泡').toBeUndefined()
    expect(bBubble.content).toBe('乙的回复')
  })

  it('撤回通知那条流：先放下旧流、登记自己的归属，也能被放下（解锁）', async () => {
    const a = startStream()

    useAppStore.getState()._sendRevokeNotice()
    const b = h.calls[h.calls.length - 1]

    expect(a.aborted, '撤回通知没先把旧流放下').toBe(true)
    const stream = useAppStore.getState()._chatStream
    expect(stream, '撤回通知流没登记归属').toBeTruthy()
    expect(stream.cid).toBe(lastCid())
    expect(useAppStore.getState().sending).toBe(true)

    useAppStore.getState()._cancelChatStream()
    await flush()

    expect(b.aborted).toBe(true)
    expect(useAppStore.getState().sending, '发送中没解锁').toBe(false)
  })

  it('回复途中列表末尾多了别的消息：token 仍只写进自己的气泡', async () => {
    const a = startStream('你好')
    const cid = lastCid()

    useAppStore.setState((s) => ({
      messages: [...s.messages, { _cid: 'm_extra', role: 'char', content: '别的消息' }],
    }))

    a.emitToken('回复')
    await flush()

    const msgs = useAppStore.getState().messages
    expect(msgs.find((m) => m._cid === cid).content, 'token 写到最后一条去了').toBe('回复')
    expect(msgs[msgs.length - 1].content).toBe('别的消息')
  })
})
