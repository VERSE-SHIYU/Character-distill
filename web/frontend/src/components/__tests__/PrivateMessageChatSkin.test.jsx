import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import ChatInputBar from '../common/ChatInputBar'
import PrivateMessageChat from '../PrivateMessageChat'

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

const PEER = 'u_peer'

const { serverMsgs, setServerMsgs, serverReactions, setServerReactions } = vi.hoisted(() => {
  const msgs = []
  const rxn = {}
  return {
    serverMsgs: msgs,
    setServerMsgs: (m) => { msgs.length = 0; msgs.push(...m) },
    serverReactions: rxn,
    setServerReactions: (r) => Object.assign(rxn, r),
  }
})

vi.mock('../../store/useAppStore', () => {
  const state = {
    authUser: { id: 'u_me', username: 'me' },
    userAvatar: null,
    currentCard: null,
    affinity: null,
    fetchAffinity: vi.fn(),
    refreshUnread: vi.fn(),
  }
  const hook = (sel) => sel(state)
  hook.getState = () => state
  hook.setState = (patch) => Object.assign(state, typeof patch === 'function' ? patch(state) : patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url) => {
    if (url.includes('/reactions')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ reactions: serverReactions }) })
    }
    if (url.includes('/messages/with/')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ messages: serverMsgs }) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
  }),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../components/common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))

beforeEach(() => {
  setServerMsgs([])
  setServerReactions({})
})

describe('私聊输入区皮肤（composer-bar--dm）+ 背景光晕', () => {
  it('ChatInputBar variant="dm" 时根节点含 composer-bar--dm；不传时不含', () => {
    const { container } = render(<ChatInputBar onSend={() => {}} variant="dm" />)
    const root = container.querySelector('.composer-bar')
    expect(root).toBeTruthy()
    expect(root.classList.contains('composer-bar--dm')).toBe(true)

    const { container: plain } = render(<ChatInputBar onSend={() => {}} />)
    const root2 = plain.querySelector('.composer-bar')
    expect(root2.classList.contains('composer-bar--dm')).toBe(false)
  })

  it('PrivateMessageChat 渲染出的 composer 根节点含 composer-bar--dm', async () => {
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelector('.dm-composer .composer-bar')).toBeTruthy())
    expect(container.querySelector('.dm-composer .composer-bar').classList.contains('composer-bar--dm')).toBe(true)
  })

  it('PrivateMessageChat 渲染出 .dm-bg-glow 元素', async () => {
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelector('.dm-bg-glow')).toBeTruthy())
  })

  it('私聊页顶栏结构不变（回归：ChatArea 改版不得影响私聊页）', async () => {
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelector('.dm-header')).toBeTruthy())
    expect(container.querySelector('.dm-back')).toBeTruthy()
    expect(container.querySelector('.dm-peer-name')?.textContent).toBe('沈星回')
    expect(container.querySelector('.chat-topbar-compact')).toBeNull()
  })
})
