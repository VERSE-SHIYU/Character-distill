import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CharCard from '../CharCard'

// 角色管理页的页头在「没选文本」的空态下也必须在：标题、返回、蒸馏工作台入口。
// 之前页头只写在有文本的分支里，空态没有任何返回路径。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../api/client', () => ({ fetchWithTimeout: vi.fn(() => Promise.resolve({ json: () => ({}) })), getAuthHeaders: vi.fn(() => ({})), exportCard: vi.fn() }))
vi.mock('../../store/db', () => ({ saveAvatar: vi.fn(), getAvatar: vi.fn(() => Promise.resolve(null)), loadCardAvatar: vi.fn(() => Promise.resolve(null)) }))
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

describe('角色管理页页头（有无文本）', () => {
  beforeEach(() => { for (const k of Object.keys(mockState)) delete mockState[k]; Object.assign(mockState, base(), { getState: () => mockState }) })

  it('E1 没选文本：页头仍在，有返回与蒸馏工作台入口，内容为空态', () => {
    mockState.currentTextId = null
    render(<CharCard />)
    const header = screen.getByRole('heading', { name: '角色管理' }).closest('.page-header')
    expect(header).toContainElement(screen.getByRole('button', { name: '返回' }))
    expect(header).toContainElement(screen.getByRole('button', { name: '蒸馏工作台' }))
    expect(screen.getByText('请先选择一份文本')).toBeVisible()
    expect(screen.queryByText(/当前文本：/)).toBeNull()
  })

  it('E2 没选文本：点返回会离开本页', () => {
    mockState.currentTextId = null
    render(<CharCard />)
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(mockState.navigateBack).toHaveBeenCalled()
  })

  it('E3 有文本：行为不变，显示当前文本、不显示空态', () => {
    render(<CharCard />)
    expect(screen.getByText('当前文本：a.md')).toBeInTheDocument()
    expect(screen.queryByText('请先选择一份文本')).toBeNull()
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument()
  })
})
