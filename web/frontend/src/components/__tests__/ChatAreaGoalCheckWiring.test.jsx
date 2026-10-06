import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import ChatArea from '../ChatArea'

// §1 目标检查的接线：kbd_check 打开时，角色聊天页聚焦输入框后 800ms/2000ms
// 要真的量到 C1–C3。这里用真 ChatInputBar（不 mock），所以选择器写错或
// bottomRef.previousElementSibling 取不到，会走「找不到输入栏或最后一条消息」
// 分支——下面的断言就是防这个。

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
vi.mock('../../components/common/ChatSessionList', () => ({ default: () => null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))
vi.mock('../../components/common/Loading', () => ({ default: () => null }))
vi.mock('../../components/common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../../components/common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../../components/PageHeader', () => ({ default: () => null }))

const overlay = () => document.querySelector('[data-kbd-goalcheck]')

describe('ChatArea 接线：目标检查', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.visualViewport = {
      offsetTop: 0,
      height: 800,
      addEventListener: () => {},
      removeEventListener: () => {},
    }
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    localStorage.clear()
    delete window.visualViewport
  })

  it('开关关闭时聚焦输入框不产生任何覆盖层', () => {
    const { container } = render(<ChatArea />)
    container.querySelector('.chat-textarea')?.focus()
    vi.advanceTimersByTime(2000)
    expect(overlay()).toBeNull()
  })

  it('kbd_check=1 时聚焦输入框后量到三项（证明找得到输入栏和末条）', () => {
    localStorage.setItem('kbd_check', '1')
    const { container } = render(<ChatArea />)

    const ta = container.querySelector('.chat-textarea')
    expect(ta, '真 ChatInputBar 没渲染，接线无从谈起').not.toBeNull()
    ta.focus()

    vi.advanceTimersByTime(800)
    expect(overlay()).not.toBeNull()
    const text = overlay().textContent
    expect(text).not.toContain('找不到')
    expect(text).toContain('C1')
    expect(text).toContain('C2')
    expect(text).toContain('C3')

    vi.advanceTimersByTime(1200)
    expect(overlay().textContent).toContain('2000ms')
  })
})
