import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import ChatArea from '../ChatArea'

// 聊天界面不再渲染「检索来源」（spec: docs/specs/hide-evidence-rail-v2.md）。
// 后端照旧发 evidence 帧、消息仍带 evidence 字段 —— 这里考的是前端收到后不渲染任何节点。
//
// 反空过：同一条 char 消息的正文必须真的渲染出来。否则「没有 rail」可能只是
// 整条消息压根没渲染（ChatBubble 若被 mock 成 null 就会这样），那种绿什么都没证明。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null,
      dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

const { mockState } = vi.hoisted(() => {
  const state = {
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
    sessionId: 's1',
    currentView: 'chat',
    resumeLoading: false,
    chatSnapshot: null,
    archiveModalOpen: false,
    _pendingChatCardId: null,
    messages: [{
      role: 'char',
      id: 'm1',
      content: '角色回复正文',
      timestamp: '2026-01-01T00:00:00Z',
      evidence: [
        { source: 'scene', status: 'hit', items: [{ text: '证据原文甲' }] },
        { source: 'memory', status: 'hit', items: [{ text: '证据原文乙' }] },
      ],
    }],
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
    authUser: { id: 'u1', role: 'user' },
    cardAvatars: {},
    userAvatar: null,
    currentSessionAvatar: null,
    voiceList: [],
    viewHistory: [],
    loadVoices: () => {},
    clearMessageTyping: () => {},
  }
  return { mockState: state }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../../components/common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../../components/common/ChatInputBar', () => ({ default: () => null }))
vi.mock('../../components/common/ChatSessionList', () => ({ default: () => null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))
vi.mock('../../components/common/Loading', () => ({ default: () => null }))
vi.mock('../../components/common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../../components/common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../../components/common/MessageReactions', () => ({ default: () => null }))
vi.mock('../../components/common/ReplyQuote', () => ({ default: () => null }))
vi.mock('../../components/PageHeader', () => ({ default: () => null }))
// 注意：**不** mock ChatBubble —— 它必须把 children 渲染出来，rail 才有机会出现。

// 每个 rail 专属类名都点名断言：删掉渲染点后一个都不该存在
const RAIL_SELECTORS = [
  '.evidence-rail', '.evidence-toggle', '.evidence-summary',
  '.evidence-card', '.evidence-item', '.evidence-item-text', '.evidence-body',
]

describe('ChatArea：带 evidence 的 char 消息不渲染检索来源', () => {
  it('渲染了消息正文，但没有任何检索来源节点', () => {
    const { queryByText } = render(<ChatArea />)

    // 反空过：消息真的渲染了
    expect(document.querySelector('.chat-msg-char')).not.toBeNull()
    expect(document.body.textContent).toContain('角色回复正文')

    // 先查文案再查类名：类名可以两处一起改名而文案不变，两条断言守的不是同一件事。
    expect(queryByText(/检索来源/)).toBeNull()
    for (const sel of RAIL_SELECTORS) {
      expect(document.querySelector(sel), `${sel} 不该存在`).toBeNull()
    }
  })
})
