import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import ChatArea from '../ChatArea'
import { fetchWithTimeout } from '../../api/client'

// 记忆面板的写失败原先走 window.alert，且面板是 fixed 覆盖层 —— ChatView 顶层那个
// ErrorBox 会被它盖住，所以错误必须落在面板自己身上。
// 全仓不再用 alert 报写失败：这条是它的红源。

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

let alertSpy

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
    if (opts?.method === 'POST' || opts?.method === 'PUT') {
      return Promise.reject(Object.assign(new Error('添加失败，请稍后再试'), { status: 500 }))
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
  })
  alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
})

const openMemoryPanel = async () => {
  render(<ChatArea />)
  fireEvent.click(document.querySelector('[data-more-trigger]'))
  fireEvent.click([...document.querySelectorAll('.chat-more-item')].find((b) => b.textContent.includes('角色记忆')))
  await waitFor(() => expect(document.querySelector('.memory-panel')).toBeInTheDocument())
}

describe('记忆面板：写失败显示在面板里', () => {
  it('添加记忆失败：面板内出现 error-box，且不再 alert', async () => {
    await openMemoryPanel()

    fireEvent.click(document.querySelector('.memory-add-trigger'))
    fireEvent.change(document.querySelector('.memory-add-input'), { target: { value: '新记忆' } })
    fireEvent.click([...document.querySelectorAll('.memory-add-actions button')].find((b) => b.textContent === '保存'))

    await waitFor(() => expect(document.querySelector('.memory-panel .error-box')).toBeInTheDocument())
    expect(document.querySelector('.memory-panel .error-box').textContent).toContain('添加失败，请稍后再试')
    expect(alertSpy).not.toHaveBeenCalled()
  })
})
