import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import AuthorPage from '../AuthorPage'
import GlobalSearchBox from '../common/GlobalSearchBox'
import useAppStore from '../../store/useAppStore'
import { fetchWithTimeout, globalSearch } from '../../api/client'

// 跨区用户发现（spec cross-region-user-search）：
//   搜索框 —— 对端用户标「跨区」；请求失败要说出来，不能和「无结果」一样不出现。
//   作者主页 —— 对端视图只有资料 + 公开角色（不能关注 / 使用 / 看书架动态）；
//               禁用视图只有身份 + 封禁提示（连私信也不给）。
// canWrite 固定为 true：被隐藏的按钮是因为 is_remote / is_disabled，而不是因为没写权限。

vi.mock('../../hooks/useCanWrite', () => ({ default: () => true }))
vi.mock('../../hooks/useSwipeBack', () => ({ default: () => ({}) }))
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  globalSearch: vi.fn(),
}))

// 与后端规则表（web/account_visibility.py::CAPABILITIES，后端 test_capability_table 锁定）同值
const ALL = { message: true, follow: true, fork: true, cards: true, stats: true, bookshelf: true, posts: true, presence: true }
const NONE = Object.fromEntries(Object.keys(ALL).map((k) => [k, false]))
const CAPS = {
  local: ALL,
  remote: { ...NONE, message: true, cards: true },
  disabled: NONE,
}

const ok = (body) => Promise.resolve({ ok: true, status: 200, json: async () => body })

function routeAuthor(authorBody) {
  fetchWithTimeout.mockImplementation((url) => {
    if (url.includes('/posts')) return ok({ posts: [] })
    return ok(authorBody)
  })
}

const remoteCard = { id: 'rc1', name: 'Far Card', card_json: '{"name":"Far Card"}', avatar_data: '' }

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.setState({ authorUserId: 'peer1', authUser: { id: 'me' } })
})

describe('AuthorPage — peer-node user', () => {
  it('shows the cross-region tag and public cards, hides follow / use / bookshelf / posts', async () => {
    routeAuthor({
      author: { id: 'peer1', username: 'farfriend', avatar_data: '', home_region: 'sg-singapore' },
      cards: [remoteCard], view: 'remote', capabilities: CAPS.remote,
    })
    render(<AuthorPage />)
    await screen.findByText('farfriend')
    expect(screen.getByText('跨区')).toBeTruthy()
    expect(screen.getByText('发私信')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '关注' })).toBeNull()
    expect(screen.queryByText('使用')).toBeNull()
    expect(screen.queryByText(/书架/)).toBeNull()
    expect(screen.queryByText('动态')).toBeNull()
    expect(screen.queryByText(/粉丝/)).toBeNull()
    expect(screen.getByText(/公开角色 \(1\)/)).toBeTruthy()
  })

  it('does not render an offline time when presence is absent', async () => {
    routeAuthor({
      author: { id: 'peer1', username: 'farfriend', avatar_data: '', home_region: 'sg-singapore' },
      cards: [], view: 'remote', capabilities: CAPS.remote,
    })
    render(<AuthorPage />)
    await screen.findByText('farfriend')
    expect(screen.queryByText('在线')).toBeNull()
    expect(document.querySelector('.author-name span[style*="border-radius"]')).toBeNull()
  })
})

describe('AuthorPage — disabled account', () => {
  it('identity + notice only, no DM, no content', async () => {
    routeAuthor({
      author: { id: 'peer1', username: 'banned1', home_region: 'cn-shenzhen' },
      cards: [], view: 'disabled', capabilities: CAPS.disabled,
    })
    render(<AuthorPage />)
    await screen.findByText('banned1')
    expect(screen.getByText('该账号已被封禁，内容无法查看')).toBeTruthy()
    expect(screen.queryByText('发私信')).toBeNull()
    expect(screen.queryByRole('button', { name: '关注' })).toBeNull()
    expect(screen.queryByText(/公开角色/)).toBeNull()
    expect(screen.queryByText(/书架/)).toBeNull()
  })
})

describe('AuthorPage — local user (unchanged)', () => {
  it('keeps follow, bookshelf and stats', async () => {
    routeAuthor({
      author: { id: 'peer1', username: 'nearby', avatar_data: '' },
      cards: [], texts: [], view: 'local', capabilities: CAPS.local, online: false,
    })
    render(<AuthorPage />)
    await screen.findByText('nearby')
    expect(screen.queryByText('跨区')).toBeNull()
    expect(screen.getByRole('button', { name: '关注' })).toBeTruthy()
    expect(screen.getByText(/书架/)).toBeTruthy()
    expect(screen.getByText(/粉丝/)).toBeTruthy()
  })
})

describe('AuthorPage — capabilities are the only switch', () => {
  it('renders nothing actionable when capabilities are missing (default deny)', async () => {
    routeAuthor({ author: { id: 'peer1', username: 'mystery' }, cards: [remoteCard], view: 'local' })
    render(<AuthorPage />)
    await screen.findByText('mystery')
    expect(screen.queryByText('发私信')).toBeNull()
    expect(screen.queryByRole('button', { name: '关注' })).toBeNull()
    expect(screen.queryByText(/公开角色/)).toBeNull()
  })
})

describe('GlobalSearchBox', () => {
  async function type(value) {
    vi.useFakeTimers()
    render(<GlobalSearchBox />)
    fireEvent.change(screen.getByPlaceholderText('搜索角色、文本、用户…'), { target: { value } })
    await act(async () => { vi.advanceTimersByTime(350) })
    vi.useRealTimers()
  }

  it('tags peer-node users as cross-region', async () => {
    globalSearch.mockResolvedValue({
      cards: [], texts: [],
      users: [
        { id: 'a', username: 'near', nickname: '', is_remote: false },
        { id: 'b', username: 'far', nickname: '', is_remote: true },
      ],
    })
    await type('x')
    await waitFor(() => expect(screen.getByText('@far')).toBeTruthy())
    const tags = screen.getAllByText('跨区')
    expect(tags).toHaveLength(1)
    expect(tags[0].closest('button').textContent).toContain('@far')
  })

  it('says the search failed instead of showing nothing', async () => {
    globalSearch.mockRejectedValue(new Error('500'))
    await type('x')
    await waitFor(() => expect(screen.getByText('搜索失败，请稍后重试')).toBeTruthy())
    expect(screen.queryByText('未找到相关内容')).toBeNull()
  })

  it('still says nothing was found on an empty result', async () => {
    globalSearch.mockResolvedValue({ cards: [], texts: [], users: [] })
    await type('x')
    await waitFor(() => expect(screen.getByText('未找到相关内容')).toBeTruthy())
  })
})
