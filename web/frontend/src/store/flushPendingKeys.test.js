import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import useAppStore from './useAppStore'

// 「重试」必须把当前 pending 的 key 一起送到后端。
//
// 后端队列在内存里，会话被空闲清理逐出后它就没了；而库里可能早就有那条消息（写成功了、
// 只是响应没回到前端）。不带 key 的话后端只补写，读数里什么都没有 —— 那条消息永远停在
// 「未保存」，刷新才恢复。带上 key，后端才答得上「它到底存没存」。
//
// 从**真** `flushMessages` 打进去（只桩 `fetch`）：把 store 的 action 也桩掉的话，请求体
// 那一段漏了这条用例照样绿 —— 那就没测到真正会坏的地方。

const jsonResponse = (body) => ({
  ok: true, status: 200, json: async () => body,
})

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  useAppStore.setState({
    sessionId: 's1',
    messages: [
      { _cid: 'm_prev', role: 'char', content: '没落库的', id: null, saveState: 'pending', saveKey: 'k1' },
      { _cid: 'm_bad', role: 'user', content: '判死的', id: null, saveState: 'failed', saveKey: 'k2' },
      { _cid: 'm_ok', role: 'user', content: '落库了的', id: 9 },
    ],
    sending: false,
    error: null,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('重试时带上 pending 的 key', () => {
  it('请求体里带上且只带上 pending 的 key，读数回来那条标记消失', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({
      flushed: [{ key: 'k1', id: 42 }], dropped: [],
    }))
    vi.stubGlobal('fetch', fetchMock)

    await useAppStore.getState().flushMessages()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/chat/s1/flush')
    expect(JSON.parse(init.body)).toEqual({ keys: ['k1'] })

    const msgs = useAppStore.getState().messages
    expect(msgs[0].id).toBe(42)
    expect(msgs[0].saveState).toBeUndefined()
    expect(msgs[1].saveState).toBe('failed')  // 判死的没被这次读数提到，原样
  })
})
