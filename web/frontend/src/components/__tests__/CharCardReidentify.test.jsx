import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor, within } from '@testing-library/react'
import CharCard from '../CharCard'
import useAppStore from '../../store/useAppStore'
import { postJSON, fetchWithTimeout } from '../../api/client'

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../api/client', () => ({
  postJSON: vi.fn(),
  fetchWithTimeout: vi.fn(() => Promise.resolve({ json: () => [] })),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
}))
vi.mock('../../store/db', () => ({ saveAvatar: vi.fn(), getAvatar: vi.fn(() => Promise.resolve(null)), loadCardAvatar: vi.fn(() => Promise.resolve(null)) }))
vi.mock('../common/GlobalSearchBox', () => ({ default: () => null }))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))

const initial = useAppStore.getState()

const openConfirm = () => {
  act(() => { fireEvent.click(screen.getByRole('button', { name: '重新识别' })) })
  return screen.getByText('重新识别人物？').closest('.modal-card')
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.setState(initial, true)
  useAppStore.setState({
    authUser: { id: 'u1', role: 'user' },
    currentTextId: 't1',
    texts: [{ id: 't1', filename: 'a.md' }],
    cards: [],
    identifiedChars: [],
    identifying: false,
    error: null,
  })
  postJSON.mockResolvedValue({ characters: [] })
  // 照生产接口：GET /roster 回库里已存的名单（`{characters}`），其余 GET 回空数组。
  serverRoster = []
  fetchWithTimeout.mockImplementation((url) => Promise.resolve({
    json: () => (String(url).includes('/api/distill/roster/') ? { characters: serverRoster } : []),
  }))
})

let serverRoster = []
const withServerRoster = (chars) => {
  serverRoster = chars
  useAppStore.setState({ identifiedChars: chars })
}

describe('D24 ①b 第 3 步：重新识别', () => {
  it('已有名单时点「重新识别」先弹确认框，不发请求', () => {
    withServerRoster([{ name: '甲' }])
    render(<CharCard />)

    const modal = openConfirm()

    expect(modal).toBeTruthy()
    expect(postJSON).not.toHaveBeenCalled()
  })

  it('确认后带 force=true 请求识别', async () => {
    withServerRoster([{ name: '甲' }])
    postJSON.mockResolvedValue({ characters: [{ name: '乙' }] })
    render(<CharCard />)
    const modal = openConfirm()

    await act(async () => {
      fireEvent.click(within(modal).getByRole('button', { name: '重新识别' }))
    })

    expect(postJSON).toHaveBeenCalledWith('/api/distill/identify', { text_id: 't1', force: true })
  })

  it('取消不发请求', async () => {
    withServerRoster([{ name: '甲' }])
    render(<CharCard />)
    const modal = openConfirm()

    await act(async () => {
      fireEvent.click(within(modal).getByRole('button', { name: '取消' }))
    })

    expect(postJSON).not.toHaveBeenCalled()
    expect(screen.queryByText('重新识别人物？')).toBeNull()
  })

  it('无名单时点「开始蒸馏」不弹框，请求 force=false', async () => {
    render(<CharCard />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '开始蒸馏' }))
    })

    expect(screen.queryByText('重新识别人物？')).toBeNull()
    expect(postJSON).toHaveBeenCalledWith('/api/distill/identify', { text_id: 't1', force: false })
  })

  it('重新识别失败后名单仍在，error 有值', async () => {
    withServerRoster([{ name: '甲' }])
    postJSON.mockRejectedValue(new Error('boom'))
    render(<CharCard />)
    const modal = openConfirm()

    await act(async () => {
      fireEvent.click(within(modal).getByRole('button', { name: '重新识别' }))
    })

    await waitFor(() => expect(useAppStore.getState().error).toBe('boom'))
    expect(useAppStore.getState().identifiedChars).toEqual([{ name: '甲' }])
    expect(screen.getByText('甲')).toBeTruthy()
  })
})

describe('D24 ①b 回归：进角色页直接显示已存名单（不花钱）', () => {
  // 进角色页的入口是 store 的 selectText / openCharacterList（它们先清空名单，再与卡片
  // 一起读回）；刷新页面时 currentTextId 不持久化，不会绕开这两个入口。
  it('从对话页进角色列表（openCharacterList）也读回名单', async () => {
    serverRoster = [{ name: '丙' }]
    await act(async () => { useAppStore.getState().openCharacterList('t1') })
    await waitFor(() => expect(useAppStore.getState().identifiedChars).toEqual([{ name: '丙' }]))
    expect(postJSON).not.toHaveBeenCalled()
  })

  it('库里有名单：进页就显示，不发识别请求', async () => {
    serverRoster = [{ name: '甲' }, { name: '乙' }]
    await act(async () => { await useAppStore.getState().selectText('t1') })
    render(<CharCard />)

    await waitFor(() => expect(screen.getByText('乙')).toBeTruthy())
    expect(fetchWithTimeout).toHaveBeenCalledWith('/api/distill/roster/t1')
    expect(postJSON).not.toHaveBeenCalled()
  })

  it('库里没有名单：进页不自动识别（打开页面不产生费用）', async () => {
    await act(async () => { await useAppStore.getState().selectText('t1') })
    render(<CharCard />)

    await waitFor(() => expect(fetchWithTimeout).toHaveBeenCalledWith('/api/distill/roster/t1'))
    expect(postJSON).not.toHaveBeenCalled()
    expect(useAppStore.getState().identifiedChars).toEqual([])
  })
})

describe('侧栏：已有卡片与识别结果上下分栏（11-03 卡片被名单挤没）', () => {
  const card = { id: 'c1', name: '卡片甲', text_id: 't1', card_json: JSON.stringify({ name: '卡片甲', identity: '身份' }) }

  it('两块都有：卡片在上栏、识别结果在下栏，中间可拖动', async () => {
    serverRoster = [{ name: '名单乙' }]
    useAppStore.setState({ cards: [card], identifiedChars: [{ name: '名单乙' }] })
    const { container } = render(<CharCard />)

    expect(screen.getByRole('separator')).toBeTruthy()
    expect(container.querySelector('[data-pane="top"]').textContent).toContain('卡片甲')
    expect(container.querySelector('[data-pane="bottom"]').textContent).toContain('名单乙')
  })

  it('只有识别结果：不分栏', () => {
    withServerRoster([{ name: '名单乙' }])
    render(<CharCard />)
    expect(screen.queryByRole('separator')).toBeNull()
    expect(screen.getByText('名单乙')).toBeTruthy()
  })
})
