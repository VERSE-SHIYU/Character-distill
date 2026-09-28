import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import PrivateMessageChat from '../PrivateMessageChat'
import usePresence from '../../hooks/usePresence'

// 调用点接线：私信页查对方 id、以 30 秒刷新（原 setInterval 的节奏），并按结果渲染头部状态。
// hook 自身的请求与缓存行为见 hooks/__tests__/usePresence.test.jsx。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../hooks/usePresence', () => ({ default: vi.fn() }))
vi.mock('../../store/useAppStore', () => {
  const state = { authUser: { id: 'u_me', username: 'me' }, userAvatar: null, currentCard: null, affinity: null, fetchAffinity: vi.fn(), refreshUnread: vi.fn() }
  const hook = (sel) => sel(state)
  hook.getState = () => state
  hook.setState = (patch) => Object.assign(state, typeof patch === 'function' ? patch(state) : patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../components/common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))

beforeEach(() => vi.mocked(usePresence).mockReset())

describe('私信页在线状态接线', () => {
  it('D1 查对方 id，30 秒刷新', () => {
    vi.mocked(usePresence).mockReturnValue({ online: true, hidden: false, lastActive: '' })
    render(<PrivateMessageChat otherUserId="u_peer" otherUsername="沈星回" />)
    expect(usePresence).toHaveBeenCalledWith('u_peer', { refreshInterval: 30000 })
  })

  it('D2 在线时显示「当前在线」', () => {
    vi.mocked(usePresence).mockReturnValue({ online: true, hidden: false, lastActive: '' })
    render(<PrivateMessageChat otherUserId="u_peer" otherUsername="沈星回" />)
    expect(screen.getByText('当前在线')).toBeInTheDocument()
  })

  it('D3 对方隐藏时不显示状态行', () => {
    vi.mocked(usePresence).mockReturnValue({ online: null, hidden: true, lastActive: '' })
    const { container } = render(<PrivateMessageChat otherUserId="u_peer" otherUsername="沈星回" />)
    expect(container.querySelector('.dm-peer-status')).toBeNull()
  })
})
