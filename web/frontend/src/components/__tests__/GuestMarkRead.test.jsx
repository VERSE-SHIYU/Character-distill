import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import PrivateMessageChat from '../PrivateMessageChat'

// 打开私信会话会自动 POST /api/messages/read/{uid}，失败被 catch 吞掉（108 的同类形态）。
// 游客必然 403，所以游客不发这个请求；普通用户照发。
// 单独一个文件是因为既有的 PrivateMessageChat* 测试把 authUser 固定成普通用户。

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

const PEER = 'u_peer'

const store = vi.hoisted(() => ({
  authUser: { id: 'u_me', username: 'me', role: 'user' },
  userAvatar: null, currentCard: null, affinity: null,
  fetchAffinity: () => {}, refreshUnread: () => {},
}))
vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(store)
  hook.getState = () => store
  hook.setState = (patch) => Object.assign(store, patch)
  return { default: hook }
})

const calls = vi.hoisted(() => [])
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url, opts) => {
    calls.push({ url, method: opts?.method })
    if (url.includes('/messages/with/')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ messages: [] }) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
  }),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../components/common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))
vi.mock('../../components/common/MessageReactions', () => ({ default: () => null }))
vi.mock('../../components/common/ChatInputBar', () => ({ default: () => null }))

const readCalls = () => calls.filter((c) => c.url.includes('/api/messages/read/'))

beforeEach(() => {
  calls.length = 0
})

describe('私信自动标记已读只发给能写的账号', () => {
  it('游客：一个 /api/messages/read/ 都不发', async () => {
    store.authUser = { id: 'u_me', username: 'me', role: 'guest' }

    render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)

    // 先等到它会话拉起来了，再断言「读请求没混在里面」——否则可能只是还没跑
    await waitFor(() => expect(calls.some((c) => c.url.includes('/messages/with/'))).toBe(true))
    expect(readCalls()).toHaveLength(0)
  })

  it('普通用户：照发标记已读', async () => {
    store.authUser = { id: 'u_me', username: 'me', role: 'user' }

    render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)

    await waitFor(() => expect(readCalls()).toHaveLength(1))
    expect(readCalls()[0].method).toBe('POST')
  })
})
