import { describe, it, expect, vi } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'
import ChatArea from '../ChatArea'
import GroupChatPage from '../GroupChatPage'

// 94 口径统一（补 1）：「未保存」的那条不能给需要服务端 id 的操作入口。
// 存失败时前端留下的是临时 id，拿它去引用 / 反应都会 422 —— 用户下一条消息就发不出去。
// MessageReactions 不 mock：入口在不在正是被测的东西。

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

// 导演模式下用户消息渲染成旁白行（本来就没有反应/引用入口），要测的是气泡分支。
const GROUP = {
  id: 'g1',
  name: '测试群',
  card_ids: JSON.stringify(['c1', 'c2']),
  user_persona_type: 'stranger',
  user_persona_name: '我',
}

const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
    sessionId: 's1',
    currentView: 'chat',
    resumeLoading: false,
    chatSnapshot: null,
    archiveModalOpen: false,
    _pendingChatCardId: null,
    messages: [],
    sending: false,
    userRolesByCard: {},
    sessionUserRole: '',
    currentTextId: 't1',
    texts: [],
    voiceStatus: null,
    isRecording: false,
    recordingDuration: 0,
    revokeCooldown: 0,
    webSearchEnabled: false,
    agentMode: false,
    affinity: null,
    affinityEnabled: true,
    authUser: { id: 'u1' },
    cardAvatars: {},
    userAvatar: null,
    currentSessionAvatar: null,
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
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

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
          // 用户气泡与角色气泡各一对 —— 群聊那两个调用点各写了一份判断，两处都要有红源。
          messages: [
            { id: 'optimistic-1', role: 'user', content: '没存上的那条', speaker: '我', created_at: '2026-09-01T10:00:00', reactions: [], saveState: 'pending' },
            { id: 7, role: 'user', content: '存上了的那条', speaker: '我', created_at: '2026-09-01T10:01:00', reactions: [] },
            { id: 'optimistic-2', role: 'assistant', content: '没存上的回复', speaker: '张三', card_id: 'c1', created_at: '2026-09-01T10:02:00', reactions: [], saveState: 'pending' },
            { id: 8, role: 'assistant', content: '存上的回复', speaker: '张三', card_id: 'c1', created_at: '2026-09-01T10:03:00', reactions: [] },
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

describe('未保存的消息不给需要 id 的入口', () => {
  it('V5 一对一：标了「未保存」那条没有引用/反应入口，普通那条有', () => {
    mutate({
      messages: [
        { _cid: 'm1', role: 'char', content: '没存上的回复', saveState: 'pending' },
        { _cid: 'm2', role: 'char', content: '存上的回复' },
      ],
    })
    const { container } = render(<ChatArea />)

    const bars = container.querySelectorAll('.msg-quick-reactions')
    expect(bars.length).toBe(1)
    expect(bars[0].closest('.chat-msg').textContent).toContain('存上的回复')
  })

  it('V4 群聊：unsaved 那条没有引用/反应入口，普通那条有', async () => {
    const { container } = render(<GroupChatPage />)

    // 从列表点进群：enterGroup 在 effect 之后再跑，currentGroupRef 已就位。
    await waitFor(() => {
      expect(container.querySelector('.messages-conv-item')).not.toBeNull()
    })
    fireEvent.click(container.querySelector('.messages-conv-item'))

    await waitFor(() => {
      expect(container.textContent).toContain('存上了的那条')
    })

    const bars = container.querySelectorAll('.msg-quick-reactions')
    expect(bars.length).toBe(2)
    for (const bar of bars) {
      const row = bar.closest('[data-msg-id]').textContent
      expect(row).toContain('存上')
      expect(row).not.toContain('没存上')
    }
  })
})
