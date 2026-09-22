import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import MinePage from '../MinePage'
import { fetchWithTimeout } from '../../api/client'

// 快捷入口只在移动端渲染（MinePage 里 `{isMobile && <EntryGrid …>}`），
// 这里把 useIsMobile 钉成 true，测的就是桌面/移动共用的那套条目过滤。
vi.mock('../../hooks/useIsMobile', () => ({ default: () => true }))

const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    authUser: null,
    authorUserId: null,
    currentView: 'mine',
    unreadTotal: 0,
    userAvatar: null,
    userBanner: null,
    fetchUserBanner: () => Promise.resolve(null),
    setAuthorUserId: () => {},
    popView: () => {},
    pushView: () => {},
    navigateTo: () => {},
    setMessageTargetUserId: () => {},
    setMessageTargetUsername: () => {},
    uploadUserBanner: () => {},
    marketPostsByUser: {},
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../PageHeader', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))
vi.mock('../common/Loading', () => ({ default: () => null }))
vi.mock('../common/Skeleton', () => ({ SkeletonCard: () => null }))
vi.mock('../common/PostCard', () => ({ default: () => null }))
vi.mock('../common/BannerCropModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

const entryLabels = async (container) => {
  await waitFor(() => expect(container.querySelector('.entry-grid')).toBeInTheDocument())
  return [...container.querySelectorAll('.entry-grid-label')].map((el) => el.textContent)
}

// 「回收站」整页都是写操作（恢复 / 彻底删除），对游客藏掉；其余五个是导航，保留。
const READ_OR_NAV = ['消息', '历史', '动态', '市场', '设置']

beforeEach(() => {
  mutate({ authUser: null, authorUserId: null, currentView: 'mine' })
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) })
})

describe('MinePage 快捷入口按身份过滤', () => {
  it('游客：回收站不出现，其余入口仍在', async () => {
    mutate({ authUser: { id: 'u1', role: 'guest' } })
    const shown = await entryLabels(render(<MinePage />).container)
    expect(shown).not.toContain('回收站')
    for (const l of READ_OR_NAV) expect(shown).toContain(l)
  })

  it('普通用户：回收站在', async () => {
    mutate({ authUser: { id: 'u1', role: 'user' } })
    const shown = await entryLabels(render(<MinePage />).container)
    expect(shown).toContain('回收站')
  })
})
