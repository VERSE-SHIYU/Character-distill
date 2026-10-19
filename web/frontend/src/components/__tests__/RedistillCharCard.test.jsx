import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import CharCard from '../CharCard'

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
vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})
const base = () => ({
  authUser: { id: 'u1', role: 'user' }, currentView: 'home', currentTextId: 't1',
  texts: [{ id: 't1', filename: 'a.md' }], cards: [{ id: 'c1', name: '祈灼', card_json: '{"name":"祈灼"}', text_id: 't1' }], cardAvatars: {}, userRolesByCard: {},
  currentCard: null, sessionId: null, unreadTotal: 0,
  setView: vi.fn(), pushView: vi.fn(), navigateTo: vi.fn(), navigateBack: vi.fn(), popView: vi.fn(),
  startChat: vi.fn(), logout: vi.fn(), setError: vi.fn(), setCardAvatar: vi.fn(),
  getUserRole: () => '', setUserRole: vi.fn(), updateCard: vi.fn(), viewCard: vi.fn(), loadCards: vi.fn(),
  identifiedChars: [{ name: '祈灼' }], identifying: false, distilling: false, distillTokenCount: 0, distillStatus: '',
  identifyCharacters: vi.fn(), distillCharacter: vi.fn(() => Promise.resolve(false)), standaloneCards: [], loadStandaloneCards: vi.fn(),
  lastDistilledCardId: null, setLastDistilledCardId: vi.fn(), restoreChatSnapshot: () => false,
  loadTexts: vi.fn(), deleteText: vi.fn(), uploadText: vi.fn(), selectText: vi.fn(), error: null, loading: false,
  uploadProgress: null, uploadTaskProgress: null, setCurrentMarketCardId: vi.fn(), setCurrentTextDetailId: vi.fn(),
})
describe('G7 角色页按钮：没启动就复原', () => {
  beforeEach(() => { for (const k of Object.keys(mockState)) delete mockState[k]; Object.assign(mockState, base()) })
  it.fails('取消后按钮回到「重新蒸馏」', async () => {
    render(<CharCard />)
    const btn = screen.getByRole('button', { name: '重新蒸馏' })
    await act(async () => { fireEvent.click(btn) })
    expect(mockState.distillCharacter).toHaveBeenCalledWith('t1', '祈灼', true)
    expect(screen.getByRole('button', { name: '重新蒸馏' })).toBeTruthy()
  })
})
