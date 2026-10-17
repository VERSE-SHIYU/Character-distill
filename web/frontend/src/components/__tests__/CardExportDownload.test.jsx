import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import CharCard from '../CharCard'
import { exportCard } from '../../api/client'

// 详情页「导出角色卡」必须走已有的 exportCard（带 a.download），而不是内联 fetch。
// 内联写法没有 a.download，浏览器会把 blob 当普通链接打开。

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
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(), getAvatar: vi.fn(() => Promise.resolve(null)), loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../common/GlobalSearchBox', () => ({ default: () => null }))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

const { mockState } = vi.hoisted(() => ({ mockState: {} }))
const base = () => ({
  authUser: { id: 'u1', role: 'user' }, currentView: 'home', currentTextId: 't1',
  texts: [{ id: 't1', filename: 'a.md' }], cards: [], cardAvatars: {}, userRolesByCard: {},
  currentCard: null, sessionId: null, unreadTotal: 0,
  setView: vi.fn(), pushView: vi.fn(), navigateTo: vi.fn(), navigateBack: vi.fn(), popView: vi.fn(),
  startChat: vi.fn(), logout: vi.fn(), setError: vi.fn(), setCardAvatar: vi.fn(),
  getUserRole: () => '', setUserRole: vi.fn(), updateCard: vi.fn(), viewCard: vi.fn(), loadCards: vi.fn(),
  identifiedChars: [], identifying: false, distilling: false, distillTokenCount: 0, distillStatus: '',
  identifyCharacters: vi.fn(), distillCharacter: vi.fn(), standaloneCards: [], loadStandaloneCards: vi.fn(),
  lastDistilledCardId: null, setLastDistilledCardId: vi.fn(), restoreChatSnapshot: () => false,
  loadTexts: vi.fn(), deleteText: vi.fn(), uploadText: vi.fn(), selectText: vi.fn(), error: null, loading: false,
  uploadProgress: null, uploadTaskProgress: null, setCurrentMarketCardId: vi.fn(), setCurrentTextDetailId: vi.fn(),
})
vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

const CARD = { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲"}' }

const renderDetail = () => {
  Object.assign(mockState, base(), {
    currentCard: CARD,
    cards: [CARD],
    identifiedChars: [{ name: '角色甲', importance: 'main', reason: '主角' }],
  })
  return render(<CharCard />)
}

const clickExport = () => fireEvent.click(screen.getByRole('button', { name: /导出角色卡/ }))

describe('详情页导出角色卡', () => {
  beforeEach(() => {
    for (const k of Object.keys(mockState)) delete mockState[k]
    vi.mocked(exportCard).mockReset()
  })

  it('点「导出角色卡」调已有的 exportCard(card.id) 一次', async () => {
    vi.mocked(exportCard).mockResolvedValue(undefined)
    renderDetail()
    clickExport()
    await waitFor(() => expect(exportCard).toHaveBeenCalledTimes(1))
    expect(exportCard).toHaveBeenCalledWith('card1')
  })

  it('exportCard 抛错时页面显示「导出失败：…」', async () => {
    vi.mocked(exportCard).mockRejectedValue(new Error('boom'))
    renderDetail()
    clickExport()
    expect(await screen.findByText('导出失败：boom')).toBeInTheDocument()
  })
})
