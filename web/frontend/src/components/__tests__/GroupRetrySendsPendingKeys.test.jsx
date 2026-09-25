import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, act } from '@testing-library/react'
import GroupChatPage from '../GroupChatPage'
import { postJSON, streamSSE } from '../../api/client'

// 群聊点「重试」时必须把当前 pending 的 key 一起送到后端。
//
// 后端队列在内存里，会话被空闲清理逐出后它就没了；而库里可能早就有那条消息（写成功了、
// 只是响应没回到前端）。不带 key 的话后端只补写，读数里什么都没有 —— 那条消息永远停在
// 「未保存」，刷新才恢复。带上 key，后端才答得上「它到底存没存」。
//
// 一对一那侧（`store/flushPendingKeys.test.js`）走的是真 store；群聊这侧的接线点在组件里，
// 是**另一处**调用点，所以另锁一条 —— 只锁 store 的话组件漏传 keys 照样全绿。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
}

const GROUP = {
  id: 'g1',
  name: '测试群',
  card_ids: JSON.stringify(['c1']),
  user_persona_type: 'stranger',
  user_persona_name: '我',
}

const { mockState } = vi.hoisted(() => ({
  mockState: {
    texts: [],
    authUser: { id: 'u1' },
    cardAvatars: {},
    userAvatar: null,
    currentSessionAvatar: null,
    messages: [],
    sending: false,
    currentTextId: 't1',
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
    currentView: 'chat',
    resumeLoading: false,
    chatSnapshot: null,
    archiveModalOpen: false,
    _pendingChatCardId: null,
    userRolesByCard: {},
    sessionUserRole: '',
    voiceStatus: null,
    isRecording: false,
    recordingDuration: 0,
    revokeCooldown: 0,
    webSearchEnabled: false,
    agentMode: false,
    affinity: null,
    affinityEnabled: false,
    voiceList: [],
    viewHistory: [],
    resumeGroupId: null,
    loadVoices: () => {},
    clearMessageTyping: () => {},
    setCardAvatar: () => {},
    setResumeGroupId: () => {},
    setView: () => {},
    popView: () => {},
    navigateTo: () => {},
    setCurrentMarketCardId: () => {},
    setInConversation: () => {},
  },
}))

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url) => {
    if (url.includes('/api/group/list')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ groups: [GROUP] }) })
    }
    if (url.includes('/history')) {
      return new Promise((resolve) => setTimeout(() => resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({
          messages: [
            { id: 7, role: 'user', content: '上一句', speaker: '我', created_at: '2026-09-01T10:00:00', reactions: [] },
          ],
        }),
      }), 0))
    }
    if (url.includes('/affinities')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
  }),
  postJSON: vi.fn(() => Promise.resolve({ flushed: [{ key: 'k1', id: 42 }], dropped: [] })),
  streamSSE: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../common/ChatSessionList', () => ({ default: () => null }))

beforeEach(() => {
  vi.mocked(streamSSE).mockReset()
  vi.mocked(postJSON).mockClear()
})

async function enterAndSend(container) {
  await waitFor(() => {
    expect(container.querySelector('.messages-conv-item')).not.toBeNull()
  })
  fireEvent.click(container.querySelector('.messages-conv-item'))
  await waitFor(() => {
    expect(container.textContent).toContain('上一句')
  })

  const textarea = container.querySelector('.chat-textarea')
  fireEvent.change(textarea, { target: { value: '这一句' } })
  fireEvent.click(container.querySelector('.chat-send-btn'))

  await waitFor(() => {
    expect(vi.mocked(streamSSE)).toHaveBeenCalled()
  })
  return vi.mocked(streamSSE).mock.calls[0]
}

describe('群聊重试时带上 pending 的 key', () => {
  it('点「重试」→ 请求体里是该群那条未保存消息的 key', async () => {
    const { container } = render(<GroupChatPage />)
    const [, , , , , , onEvent] = await enterAndSend(container)

    await act(async () => {
      onEvent({ type: 'user', msg_id: null, save: { state: 'pending', key: 'k1' } })
    })
    await waitFor(() => {
      expect(container.querySelector('.unsaved-retry')).not.toBeNull()
    })

    await act(async () => {
      fireEvent.click(container.querySelector('.unsaved-retry'))
    })

    const flushCall = vi.mocked(postJSON).mock.calls.find(([url]) => url.includes('/flush'))
    expect(flushCall).toBeDefined()
    expect(flushCall[0]).toBe('/api/group/g1/flush')
    expect(flushCall[1]).toEqual({ keys: ['k1'] })
  })
})
