import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor, within } from '@testing-library/react'
import CharCard from '../CharCard'
import useAppStore from '../../store/useAppStore'
import { postJSON } from '../../api/client'

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
})

describe('D24 ①b 第 3 步：重新识别', () => {
  it('已有名单时点「重新识别」先弹确认框，不发请求', () => {
    useAppStore.setState({ identifiedChars: [{ name: '甲' }] })
    render(<CharCard />)

    const modal = openConfirm()

    expect(modal).toBeTruthy()
    expect(postJSON).not.toHaveBeenCalled()
  })

  it('确认后带 force=true 请求识别', async () => {
    useAppStore.setState({ identifiedChars: [{ name: '甲' }] })
    postJSON.mockResolvedValue({ characters: [{ name: '乙' }] })
    render(<CharCard />)
    const modal = openConfirm()

    await act(async () => {
      fireEvent.click(within(modal).getByRole('button', { name: '重新识别' }))
    })

    expect(postJSON).toHaveBeenCalledWith('/api/distill/identify', { text_id: 't1', force: true })
  })

  it('取消不发请求', async () => {
    useAppStore.setState({ identifiedChars: [{ name: '甲' }] })
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
    useAppStore.setState({ identifiedChars: [{ name: '甲' }] })
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
