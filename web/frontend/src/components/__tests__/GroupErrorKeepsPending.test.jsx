import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, act } from '@testing-library/react'
import GroupChatPage from '../GroupChatPage'
import { streamSSE } from '../../api/client'

// V7：群聊广播以**错误帧**收尾时，那条用户消息不能从「排队等补写」翻成「发送失败」。
//
// 顺序就是缺陷的形状：用户帧先到（`save.state = "pending"` —— 后端那条消息在队列里排着，
// 下次写/重试/关停会补上），随后流本身挂了、发错误帧。这一轮的错误与那条消息的落库无关
// （挂的是助手那笔或流）。收尾若只按临时 id 就标 `_status: "failed"`，用户看到的是
// 「发送失败，点这里恢复草稿」，同一行里还挂着「未保存，刷新后会丢失」—— 两条读数打架，
// 而真相是后端会把这条补上。
//
// 变异：settle 里去掉 `!m.saveState` → 本条红。

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
      // 延后一拍：enterGroup 里 currentGroupRef 由 effect 赋值，同步 resolve 会早于它。
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
  postJSON: vi.fn(() => Promise.resolve({})),
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

describe('群聊错误帧不把排队中的消息判成发送失败', () => {
  it('先收到 pending 的用户帧，再收到错误帧 → 仍是「未保存」，不是「发送失败」', async () => {
    const { container } = render(<GroupChatPage />)
    const [, , , , onError, , onEvent] = await enterAndSend(container)

    // 用户帧：后端说这条没落库、在队列里排着（msg_id 为 null，前端保留临时 id）
    await act(async () => {
      onEvent({ type: 'user', msg_id: null, save: { state: 'pending', key: 'k1' } })
    })
    await waitFor(() => {
      expect(container.querySelector('.unsaved-hint')).not.toBeNull()
    })

    // 错误帧：这一轮挂了（挂的是助手那笔/流），与那条用户消息的落库无关
    await act(async () => {
      onError(Object.assign(new Error('连接中断，请重试'), { name: 'AppError' }))
    })

    const hint = container.querySelector('.unsaved-hint')
    expect(hint).not.toBeNull()
    expect(hint.textContent).toContain('未保存')
    expect(container.querySelector('.messages-status.failed')).toBeNull()
  })
})
