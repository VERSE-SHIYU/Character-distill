import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import useAppStore from './useAppStore'

// 一轮以**错误帧**收尾时，本轮补写成功的更早消息也要在界面上翻正：后端已把本轮的
// `flushed` / `dropped` 并进错误帧（`terminal_frame` 是 done 与 error 的唯一出口），
// 前端得把这条读数走完同一条路 —— `client.js` 把 payload 透传给 `onError`，store 的
// 收尾函数调 `applyFlushReport`。任缺一处，那条消息就永远停在「未保存」（刷新才恢复）。
//
// 从**真** `streamSSE` 打进去（只桩掉 `fetch`）：把 `streamSSE` 也桩掉的话，
// `client.js` 那一跳漏了 payload 这条用例照样绿 —— 那就没测到真正会坏的那一段。

const sseResponse = (frames) => {
  const encoder = new TextEncoder()
  const chunks = frames.map((f) => encoder.encode(`data: ${JSON.stringify(f)}\n\n`))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length
          ? { done: false, value: chunks[i++] }
          : { done: true, value: undefined }),
      }),
    },
  }
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  useAppStore.setState({
    sessionId: 's1',
    messages: [
      // 上一轮那条：库不可达时它留在了队里（`pending` + 认领用的 key），
      // 本轮写用户消息时被顺路补写掉 —— 它的真实行 id 就在本轮的读数里。
      { _cid: 'm_prev', role: 'char', content: '上一轮的话', id: null, saveState: 'pending', saveKey: 'k1' },
    ],
    sending: false,
    error: null,
    sessionUserRole: '',
    voiceEnabled: false,
    webSearchEnabled: false,
    agentMode: false,
    affinityEnabled: false,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('错误帧也要应用本轮的补写报告', () => {
  it('错误帧里带 flushed → 那条消息填回真 id、不再标「未保存」', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([
      { error: '服务暂时不可用，请稍后重试', flushed: [{ key: 'k1', id: 42 }], dropped: [] },
    ])))

    useAppStore.getState().sendMessageStream('这一句')

    await vi.waitFor(() => {
      expect(useAppStore.getState().sending).toBe(false)
    })

    const prev = useAppStore.getState().messages[0]
    expect(prev.id).toBe(42)
    expect(prev.saveState).toBeUndefined()
    expect(useAppStore.getState().error).toBe('服务暂时不可用，请稍后重试')
  })
})
