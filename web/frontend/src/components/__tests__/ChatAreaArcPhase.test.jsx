/**
 * U14 聊天头部：在「我扮演：」旁只读显示「阶段 k/n · label」；卡无阶段不显示。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import ChatArea from '../ChatArea'

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

const PHASE_CARD = JSON.stringify({
  name: '甲',
  character_arc: { phases: [{ label: '冷', state: 'x' }, { label: '中', state: 'y' }, { label: '热', state: 'z' }] },
})

const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    currentCard: { id: 'c1', name: '甲', text_id: 't1' },
    sessionId: 's1', currentView: 'chat', resumeLoading: false, chatSnapshot: null,
    archiveModalOpen: false, _pendingChatCardId: null, messages: [], sending: false,
    userRolesByCard: {}, sessionUserRole: '我', sessionArcPhase: null,
    currentTextId: 't1', texts: [], voiceStatus: null, isRecording: false, recordingDuration: 0,
    revokeCooldown: 0, webSearchEnabled: false, agentMode: false, affinity: null, affinityEnabled: true,
    authUser: { id: 'u1' }, cardAvatars: {}, userAvatar: null, currentSessionAvatar: null,
    voiceList: [], viewHistory: [], loadVoices: () => {},
  }
  return { mockState: state, mutate: (p) => Object.assign(state, p) }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (p) => Object.assign(mockState, p)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({ saveAvatar: vi.fn(), loadCardAvatar: vi.fn(() => Promise.resolve(null)) }))
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

beforeEach(() => {
  mutate({
    currentCard: { id: 'c1', name: '甲', text_id: 't1', card_json: PHASE_CARD },
    sessionArcPhase: 1, sessionUserRole: '我', messages: [], affinity: null,
  })
})

describe('U14 ChatArea 阶段显示', () => {
  it('有阶段：头部显示「阶段 k/n · label」', () => {
    const { container } = render(<ChatArea />)
    const bar = container.querySelector('.user-role-bar')
    expect(bar).toBeTruthy()
    expect(bar.textContent).toContain('阶段 1/3')
    expect(bar.textContent).toContain('冷')
  })

  it('sessionArcPhase 为 null → 显示最后阶段', () => {
    mutate({ sessionArcPhase: null })
    const { container } = render(<ChatArea />)
    const bar = container.querySelector('.user-role-bar')
    expect(bar.textContent).toContain('阶段 3/3')
  })

  it('无阶段卡：头部不显示阶段', () => {
    mutate({ currentCard: { id: 'c2', name: '乙', text_id: 't1', card_json: '{"name":"乙"}' } })
    const { container } = render(<ChatArea />)
    const bar = container.querySelector('.user-role-bar')
    expect(bar.textContent).not.toContain('阶段')
  })
})
