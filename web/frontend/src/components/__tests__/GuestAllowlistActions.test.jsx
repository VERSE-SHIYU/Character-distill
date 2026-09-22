import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import ChatInputBar from '../common/ChatInputBar'
import ArchiveListModal from '../ArchiveListModal'

// 白名单动作不许被误藏。这两条落点对游客必须仍然可见：
//   发送消息  POST /api/chat/send
//   新建存档  POST /api/distill/start_session
// 单独一个文件是因为 GuestWriteEntries.test.jsx 把 ChatInputBar mock 成 null 了。

if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false, media: query,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null,
    dispatchEvent: () => false,
  })
}

const { mockState } = vi.hoisted(() => ({
  mockState: {
    authUser: null,
    archiveModalOpen: false,
    archiveList: [],
    pendingCard: null,
    enterArchive: () => {},
    createNewArchive: () => {},
    closeArchiveModal: () => {},
  },
}))

vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

const asGuest = (node) => {
  mockState.authUser = { id: 'g1', role: 'guest' }
  return render(node).container
}

beforeEach(() => {
  mockState.authUser = null
  mockState.archiveModalOpen = false
})

describe('白名单动作：游客仍然可用', () => {
  it('聊天输入栏：guest 的输入框与「发送」按钮都在', () => {
    const c = asGuest(<ChatInputBar onSend={() => {}} />)
    expect(c.querySelector('textarea')).toBeInTheDocument()
    expect(c.querySelector('.chat-send-btn')).toBeInTheDocument()
    expect(c.querySelector('.chat-send-btn').textContent).toBe('发送')
  })

  it('存档列表：guest 的「+ 新建存档」在', () => {
    mockState.archiveModalOpen = true
    expect(asGuest(<ArchiveListModal />).querySelector('.archive-new-btn')).toBeInTheDocument()
  })
})
