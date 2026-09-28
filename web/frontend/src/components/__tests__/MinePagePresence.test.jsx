import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render as rtlRender, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import MinePage from '../MinePage'
import { fetchWithTimeout } from '../../api/client'

// 他人主页的在线状态必须是对方的，并尊重对方的隐藏设置（auth.py 返回 hidden）。

vi.mock('../../hooks/useIsMobile', () => ({ default: () => false }))
const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    authUser: { id: 'me', role: 'user' }, authorUserId: null, currentView: 'mine', unreadTotal: 0,
    userAvatar: null, userBanner: null, fetchUserBanner: () => Promise.resolve(null), setAuthorUserId: () => {},
    popView: () => {}, pushView: () => {}, navigateTo: () => {}, setMessageTargetUserId: () => {},
    setMessageTargetUsername: () => {}, uploadUserBanner: () => {}, marketPostsByUser: {},
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})
vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({ fetchWithTimeout: vi.fn(), getAuthHeaders: vi.fn(() => ({})), exportCard: vi.fn() }))
vi.mock('../PageHeader', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))
vi.mock('../common/Loading', () => ({ default: () => null }))
vi.mock('../common/Skeleton', () => ({ SkeletonCard: () => null }))
vi.mock('../common/PostCard', () => ({ default: () => null }))
vi.mock('../common/BannerCropModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

// 在线接口按 id 返回：me 在线；u2 由用例决定；其余接口返回空对象
const render = (ui) => rtlRender(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>)
let presenceOf = {}
const reply = (body) => ({ ok: true, status: 200, json: () => Promise.resolve(body) })
beforeEach(() => {
  presenceOf = { me: { online: true, hidden: false, last_active_at: '' } }
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockImplementation((url) => {
    const m = String(url).match(/^\/api\/auth\/user\/([^/]+)\/online$/)
    if (m) return Promise.resolve(reply(presenceOf[m[1]] || {}))
    if (String(url).startsWith('/api/market/author/u2')) return Promise.resolve(reply({ author: { id: 'u2', username: 'verse' } }))
    return Promise.resolve(reply({}))
  })
})
const onlineUrls = () => vi.mocked(fetchWithTimeout).mock.calls.map(([u]) => String(u)).filter((u) => u.endsWith('/online'))
const status = (c) => c.querySelector('.mine-online')

describe('MinePage 在线状态', () => {
  it('M1 他人主页查的是对方 id，不是自己', async () => {
    presenceOf.u2 = { online: false, hidden: false, last_active_at: '2026-09-27T08:00:00Z' }
    mutate({ currentView: 'author', authorUserId: 'u2' })
    const { container } = render(<MinePage />)
    await waitFor(() => expect(status(container)).toBeInTheDocument())
    expect(onlineUrls()).toContain('/api/auth/user/u2/online')
    expect(onlineUrls()).not.toContain('/api/auth/user/me/online')
    expect(status(container)).toHaveClass('off')
  })

  it('M2 对方隐藏在线状态时不显示', async () => {
    presenceOf.u2 = { online: null, last_active_at: null, hidden: true }
    mutate({ currentView: 'author', authorUserId: 'u2' })
    const { container } = render(<MinePage />)
    await waitFor(() => expect(onlineUrls()).toContain('/api/auth/user/u2/online'))
    await new Promise((r) => setTimeout(r, 30))
    expect(status(container)).toBeNull()
  })

  it('M3 自己主页仍显示自己的在线状态', async () => {
    mutate({ currentView: 'mine', authorUserId: null })
    const { container } = render(<MinePage />)
    await waitFor(() => expect(status(container)).toBeInTheDocument())
    expect(onlineUrls()).toContain('/api/auth/user/me/online')
    expect(status(container)).not.toHaveClass('off')
  })
})
