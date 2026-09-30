import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import ChatArea from '../ChatArea'
import { fetchWithTimeout } from '../../api/client'

// 长期记忆只用用户自己的 LLM key + 百炼 key（docs/specs/user-own-keys.md）。
// 没配齐时 /api/memory/list 给 configured: false：面板要说清去哪里配，而不是显示
// 「聊天中的重要信息会自动记录」（这句话此时不成立），也不给一个点了只会 409 的「添加」。

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
    authUser: { id: 'u1', role: 'user' },
    cardAvatars: {},
    userAvatar: null,
    currentSessionAvatar: null,
    voiceList: [],
    viewHistory: [],
    loadVoices: () => {},
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
  fetchWithTimeout: vi.fn(),
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


const listReturns = (payload) => {
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockImplementation(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) }))
}

const openMemoryPanel = async () => {
  render(<ChatArea />)
  fireEvent.click(document.querySelector('[data-more-trigger]'))
  fireEvent.click([...document.querySelectorAll('.chat-more-item')].find((b) => b.textContent.includes('角色记忆')))
  await waitFor(() => expect(document.querySelector('.memory-panel')).toBeInTheDocument())
}

describe('记忆面板：没配齐自己的 key 时给引导', () => {
  it('configured: false → 显示引导文案，不显示「自动记录」与「添加记忆」', async () => {
    listReturns({ memories: [], enabled: true, configured: false })
    await openMemoryPanel()

    await waitFor(() => expect(document.querySelector('.memory-unconfigured')).toBeInTheDocument())
    expect(document.querySelector('.memory-unconfigured').textContent).toContain('百炼 Key')
    expect(document.querySelector('.memory-add-trigger')).toBeNull()
    expect(document.querySelector('.memory-panel').textContent).not.toContain('暂无记忆')
  })

  it('configured: true → 照旧：有「添加记忆」、无引导', async () => {
    listReturns({ memories: [], enabled: true, configured: true })
    await openMemoryPanel()

    await waitFor(() => expect(document.querySelector('.memory-panel').textContent).toContain('暂无记忆'))
    expect(document.querySelector('.memory-add-trigger')).not.toBeNull()
    expect(document.querySelector('.memory-unconfigured')).toBeNull()
  })

  it('没配齐也能看到已有记忆', async () => {
    listReturns({ memories: [{ id: 'm1', memory: '他喜欢喝美式' }], enabled: true, configured: false })
    await openMemoryPanel()

    await waitFor(() => expect(document.querySelector('.memory-panel').textContent).toContain('他喜欢喝美式'))
    expect(document.querySelector('.memory-unconfigured')).not.toBeNull()
  })
})
