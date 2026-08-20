import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import ChatArea from '../ChatArea'
import { fetchWithTimeout } from '../../api/client'

// jsdom 缺省能力补齐（同 ChatAreaAffinity.test.jsx）
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

beforeEach(() => {
  mutate({
    affinity: null,
    affinityEnabled: true,
    messages: [],
    texts: [],
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
  })
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockResolvedValue({ status: 200, ok: true, json: () => Promise.resolve({}) })
})

describe('ChatArea 顶栏 dm-header 结构', () => {
  it('渲染返回/头像/名字/状态/3 个图标按钮', () => {
    const { container } = render(<ChatArea />)
    expect(container.querySelector('.dm-header')).toBeInTheDocument()
    expect(container.querySelector('.dm-back')).toBeInTheDocument()
    expect(container.querySelector('.dm-peer-avatar')).toBeInTheDocument()
    expect(container.querySelector('.dm-peer-name')).toBeInTheDocument()
    expect(container.querySelector('.dm-peer-status')).toBeInTheDocument()
    expect(container.querySelectorAll('.dm-icon-btn').length).toBe(3)
  })

  it('charIdentity 渲染为 .dm-peer-tag', () => {
    mutate({
      currentCard: { id: 'c1', name: '测试角色', text_id: 't1', card_json: '{"name":"测试角色","identity":"仙界剑仙"}' },
    })
    const { container } = render(<ChatArea />)
    const tags = container.querySelectorAll('.dm-peer-tag')
    expect([...tags].some((t) => t.textContent === '仙界剑仙')).toBe(true)
  })

  it('affinity=null 时内心之声按钮在、数值区不在（769064f 回归）', () => {
    const { container } = render(<ChatArea />)
    const btn = container.querySelector('[data-affinity-trigger]')
    expect(btn).toBeInTheDocument()

    fireEvent.click(btn)
    expect(container.querySelector('.inner-voice-popup')).toBeInTheDocument()
    expect(container.querySelector('.inner-voice-stats')).not.toBeInTheDocument()
  })

  it('属地：中国 IP → 显示省份标签', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce({
      status: 200,
      ok: true,
      json: () => Promise.resolve({ country: '中国', region: '广东' }),
    })
    const { container } = render(<ChatArea />)
    await waitFor(() => {
      const tags = container.querySelectorAll('.dm-peer-tag')
      expect([...tags].some((t) => t.textContent === '广东')).toBe(true)
    })
  })

  it('属地：204 无数据 → 不渲染属地标签', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce({ status: 204, ok: true, json: () => Promise.resolve({}) })
    const { container } = render(<ChatArea />)
    await waitFor(() => expect(vi.mocked(fetchWithTimeout)).toHaveBeenCalledWith('/api/market/location'))
    expect(container.querySelectorAll('.dm-peer-tag').length).toBe(0)
  })
})
