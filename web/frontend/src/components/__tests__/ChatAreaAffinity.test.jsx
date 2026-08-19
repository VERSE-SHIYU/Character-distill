import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import ChatArea from '../ChatArea'

// jsdom 缺省能力补齐（useIsMobile / useAutoScroll 依赖）
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

// 2272cbc 重构回归回归：affinity 语义改为 null|dict 后，头部心情按钮被
// `affinityEnabled && affinity &&` 门控连带隐藏（无数据=按钮消失）。
// 正确模式：按钮是常驻 affordance，只受 affinityEnabled 控制；弹层数值区
// 才用 affinity 门控。与 PrivateMessageChat.canShowInnerVoice 同构。
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
    cardAvatars: { c1: 'data:image/png;base64,test' },
    userAvatar: null,
    currentSessionAvatar: null,
    voiceList: [],
    viewHistory: [],
    loadVoices: () => {},
  }
  return {
    mockState: state,
    mutate: (patch) => Object.assign(state, patch),
  }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) })),
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
vi.mock('../../components/common/ChatBubble', () => ({ default: () => null }))
vi.mock('../../components/common/MessageReactions', () => ({ default: () => null }))
vi.mock('../../components/common/ReplyQuote', () => ({ default: () => null }))
vi.mock('../../components/PageHeader', () => ({ default: () => null }))

const AFFINITY_DATA = {
  affinity: 62,
  trust: 45,
  guard: 38,
  stage: '朋友',
  stage_emoji: '😄',
  mood: '心软',
  mood_emoji: '😊',
  inner_voice: '今天也想和你多说两句。',
}

beforeEach(() => {
  mutate({
    affinity: null,
    affinityEnabled: true,
    messages: [],
    texts: [],
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
  })
})

describe('ChatArea 内心活动按钮（affinity 门控回归）', () => {
  it('affinity=null（未评估/新会话）时按钮仍存在于头部，弹层显示占位而非数值', () => {
    const { container } = render(<ChatArea />)
    const btn = container.querySelector('[data-affinity-trigger]')
    expect(btn).toBeInTheDocument()

    fireEvent.click(btn)
    expect(container.querySelector('.inner-voice-popup')).toBeInTheDocument()
    // 数值区不渲染
    expect(container.querySelector('.inner-voice-stats')).not.toBeInTheDocument()
    expect(container.querySelector('.inner-voice-stage-pill') || container.querySelector('.stage-pill')).not.toBeInTheDocument()
    // 占位文案
    expect(container.querySelector('.inner-voice-text')?.textContent).toBe('还没有产生想法')
  })

  it('affinity 有数据时弹层渲染真实数值区', () => {
    mutate({ affinity: AFFINITY_DATA })
    const { container } = render(<ChatArea />)
    const btn = container.querySelector('[data-affinity-trigger]')
    expect(btn).toBeInTheDocument()

    fireEvent.click(btn)
    expect(container.querySelector('.inner-voice-popup')).toBeInTheDocument()
    const stats = container.querySelector('.inner-voice-stats')
    expect(stats).toBeInTheDocument()
    expect(stats?.textContent).toContain('62')
    expect(stats?.textContent).toContain('45')
    expect(stats?.textContent).toContain('38')
    expect(container.querySelector('.inner-voice-text')?.textContent).toContain('今天也想和你多说两句。')
  })
})
