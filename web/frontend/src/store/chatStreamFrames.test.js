import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { fetchWithTimeout, postJSON, streamSSE } from '../api/client'
vi.mock('../api/client', () => ({ postJSON: vi.fn(), streamSSE: vi.fn(), fetchWithTimeout: vi.fn(),
  getToken: vi.fn(), setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(), removeAuth: vi.fn() }))
let streams
const flush = () => new Promise((r) => setTimeout(r, 0))
const SYS = { role: 'system', content: '角色卡已更新', _cid: 'sys' }
const tail = () => useAppStore.setState((st) => ({ messages: [...st.messages, SYS] }))
beforeEach(() => {
  vi.resetAllMocks(); vi.spyOn(console, 'error').mockImplementation(() => {})
  streams = []
  streamSSE.mockImplementation((u, b, onToken, onDone, onError) => { streams.push({ onToken, onDone, onError }); return () => {} })
  fetchWithTimeout.mockResolvedValue({ status: 204, json: async () => ({}) })
  postJSON.mockResolvedValue({})
  useAppStore.setState({ sessionId: 'A', currentCard: { id: 'cardA', session_id: 'A' }, messages: [], sending: false, error: null,
    voiceEnabled: false, currentView: 'chat', viewHistory: [], _chatStream: null })
})
const rows = () => useAppStore.getState().messages.map((m) => [m.role, m.content, m.id ?? null, !!m.retracted])

describe('收尾帧按自己的气泡落字段（列表末尾有别的消息时）', () => {
  it('done：本轮用户消息与角色气泡各自拿到 id', async () => {
    useAppStore.getState().sendMessageStream('你好'); streams[0].onToken('在的'); tail()
    streams[0].onDone({ user_msg_id: 11, char_msg_id: 12 }); await flush()
    expect(rows()).toEqual([['user', '你好', 11, false], ['char', '在的', 12, false], ['system', '角色卡已更新', null, false]])
  })
  it('done 带 summary：摘要插在本轮用户消息之前', async () => {
    useAppStore.getState().sendMessageStream('你好'); tail()
    streams[0].onDone({ summary: '前情提要' }); await flush()
    expect(useAppStore.getState().messages.map((m) => m.role)).toEqual(['summary', 'user', 'char', 'system'])
  })
  it('done 带 retracted：标在自己的气泡上', async () => {
    useAppStore.getState().sendMessageStream('你好'); streams[0].onToken('后悔了'); tail()
    streams[0].onDone({ retracted: true }); await flush()
    expect(rows().map((r) => r[3])).toEqual([false, true, false])
  })
  it('错误帧：写 error、解锁、交出所有权', async () => {
    useAppStore.getState().sendMessageStream('你好')
    streams[0].onError(new Error('连接中断，请重试'), undefined); await flush()
    const s = useAppStore.getState()
    expect([s.sending, s._chatStream, s.error]).toEqual([false, null, '连接中断，请重试'])
  })
  it('语音开启：合成的是自己那条气泡', async () => {
    const synth = vi.fn()
    useAppStore.setState({ voiceEnabled: true, _synthesizeVoiceReply: synth, fetchAffinity: vi.fn() })
    useAppStore.getState().sendMessageStream('你好'); streams[0].onToken('在的'); tail()
    streams[0].onDone({}); await flush()
    expect(synth).toHaveBeenCalledWith('在的', 1)
  })
  it('撤回通知的 done：id 落在它自己的气泡', async () => {
    useAppStore.getState()._sendRevokeNotice(); streams[0].onToken('怎么撤回了'); tail()
    streams[0].onDone({ char_msg_id: 21 }); await flush()
    expect(rows()).toEqual([['char', '怎么撤回了', 21, false], ['system', '角色卡已更新', null, false]])
  })
})

it('撤回通知开启语音：合成的是它自己的气泡', async () => {
  const synth = vi.fn()
  useAppStore.setState({ voiceEnabled: true, _synthesizeVoiceReply: synth })
  useAppStore.getState()._sendRevokeNotice(); streams[0].onToken('怎么撤回了'); tail()
  streams[0].onDone({}); await flush()
  expect(synth).toHaveBeenCalledWith('怎么撤回了', 0)
})

it('组件拿到的取消函数也走唯一入口：取消后不冒出「请求超时」，发送解锁', async () => {
  streamSSE.mockImplementation((u, b, onToken, onDone, onError) => {
    streams.push({ onToken, onDone, onError })
    return () => queueMicrotask(() => onError(new Error('请求超时，请重试'), undefined))
  })
  const cancel = useAppStore.getState().sendMessageStream('你好')
  cancel(); await flush()
  const s = useAppStore.getState()
  expect([s.sending, s._chatStream, s.error]).toEqual([false, null, null])
})
