import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'
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

const MY = 'u_me'
const PEER = 'u_peer'

const { serverMsgs, setServerMsgs, serverReactions, setServerReactions, reactCalls } = vi.hoisted(() => {
  const arr = []
  const rxn = {}
  const calls = []
  return {
    serverMsgs: arr,
    setServerMsgs: (msgs) => { arr.length = 0; arr.push(...msgs) },
    serverReactions: rxn,
    setServerReactions: (r) => Object.assign(rxn, r),
    reactCalls: calls,
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
  hook.setState = (patch) => Object.assign(state, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url) => {
    if (url.includes('/reactions')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ reactions: serverReactions }) })
    }
    if (url.includes('/react')) {
      reactCalls.push(url)
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, added: true }) })
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
vi.mock('../../components/common/ChatInputBar', () => ({ default: () => null }))

// 生成 sqlite 形状消息：now - minsAgo 分钟
const msg = (id, content, minsAgo, over = {}) => {
  const d = new Date(Date.now() - minsAgo * 60000)
  const p = (n) => String(n).padStart(2, '0')
  return {
    id,
    sender_id: MY,
    receiver_id: PEER,
    content,
    is_read: 1,
    created_at: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`,
    cross_border_synced: 1,
    ...over,
  }
}

beforeEach(() => {
  setServerMsgs([])
  setServerReactions({})
  reactCalls.length = 0
})

const renderChat = () => render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)

describe('PrivateMessageChat 表情回应（dm-reaction）', () => {
  it('有回应的消息渲染 .dm-reaction 按钮，计数正确', async () => {
    setServerMsgs([msg('m1', 'A', 4), msg('m2', 'B', 3)])
    setServerReactions({
      m1: [{ emoji: '👍', count: 1, users: [MY] }],
      m2: [{ emoji: '🔥', count: 2, users: ['u_other'] }],
    })
    const { container } = renderChat()
    await waitFor(() => expect(container.querySelectorAll('.dm-reaction').length).toBe(2))
    const btns = [...container.querySelectorAll('.dm-reaction')]
    expect(btns[0].textContent).toContain('👍')
    expect(btns[0].textContent).toContain('1')
    expect(btns[1].textContent).toContain('🔥')
    expect(btns[1].textContent).toContain('2')
  })

  it('users 含当前用户时按钮带 .mine，不含时没有', async () => {
    setServerMsgs([msg('m1', 'A', 4), msg('m2', 'B', 3)])
    setServerReactions({
      m1: [{ emoji: '👍', count: 1, users: [MY] }],
      m2: [{ emoji: '🔥', count: 2, users: ['u_other'] }],
    })
    const { container } = renderChat()
    await waitFor(() => expect(container.querySelectorAll('.dm-reaction').length).toBe(2))
    const btns = [...container.querySelectorAll('.dm-reaction')]
    expect(btns[0].classList.contains('mine')).toBe(true)
    expect(btns[1].classList.contains('mine')).toBe(false)
  })

  it('无回应的消息不渲染 .dm-reactions 容器', async () => {
    setServerMsgs([msg('m1', 'A', 4), msg('m2', 'B', 3), msg('m3', 'C', 2)])
    setServerReactions({
      m1: [{ emoji: '👍', count: 1, users: [MY] }],
      m2: [],
    })
    const { container } = renderChat()
    await waitFor(() => expect(container.querySelectorAll('.dm-reaction').length).toBe(1))
    // 只有 m1 有容器；m2/m3 都不渲染空容器
    expect(container.querySelectorAll('.dm-reactions').length).toBe(1)
  })

  it('点击回应按钮触发 POST，本地计数乐观更新', async () => {
    setServerMsgs([msg('m1', 'A', 4), msg('m2', 'B', 3)])
    setServerReactions({
      m1: [{ emoji: '👍', count: 1, users: [MY] }],
      m2: [{ emoji: '🔥', count: 2, users: ['u_other'] }],
    })
    const { container } = renderChat()
    await waitFor(() => expect(container.querySelectorAll('.dm-reaction').length).toBe(2))
    // m2 的 🔥（非 mine）：点击后乐观 +1 → 2→3
    const btns = [...container.querySelectorAll('.dm-reaction')]
    fireEvent.click(btns[1])
    await waitFor(() => expect(container.querySelectorAll('.dm-reaction')[1].textContent).toContain('3'))
    // POST 已发出
    expect(reactCalls.length).toBe(1)
    expect(reactCalls[0]).toContain('/api/messages/m2/react')
    // 点击后变 mine
    expect(container.querySelectorAll('.dm-reaction')[1].classList.contains('mine')).toBe(true)
  })

  it('操作条包含表情按钮入口', async () => {
    setServerMsgs([msg('m1', 'A', 4)])
    setServerReactions({})
    const { container } = renderChat()
    await waitFor(() => expect(container.querySelector('.dm-bubble-actions')).toBeTruthy())
    const actions = [...container.querySelector('.dm-bubble-actions').children].map((b) => b.textContent)
    expect(actions).toContain('😊')
  })
})
