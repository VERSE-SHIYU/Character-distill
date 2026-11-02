import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

const RUNNING = (character, textId = 't1', progress = 0) => ({
  id: `k-${character}`, task_id: `k-${character}`, textId, character,
  status: 'running', done: false, progress_pct: progress, actions: ['cancel'],
})

const base = () => ({
  authUser: { id: 'u1', role: 'user' }, currentView: 'home', currentTextId: 't1',
  texts: [{ id: 't1', filename: 'a.md' }], cards: [{ id: 'c1', name: '祈灼', card_json: '{"name":"祈灼"}', text_id: 't1' }], cardAvatars: {}, userRolesByCard: {},
  currentCard: null, sessionId: null, unreadTotal: 0,
  setView: vi.fn(), pushView: vi.fn(), navigateTo: vi.fn(), navigateBack: vi.fn(), popView: vi.fn(),
  startChat: vi.fn(), logout: vi.fn(), setError: vi.fn(), setCardAvatar: vi.fn(),
  getUserRole: () => '', setUserRole: vi.fn(), updateCard: vi.fn(), viewCard: vi.fn(), loadCards: vi.fn(),
  identifiedChars: [{ name: '祈灼' }, { name: '江澄' }], identifying: false,
  distilling: false, distillTokenCount: 0, distillStatus: '', distillTasks: [],
  identifyCharacters: vi.fn(), distillCharacter: vi.fn(() => Promise.resolve(true)), standaloneCards: [], loadStandaloneCards: vi.fn(),
  lastDistilledCardId: null, setLastDistilledCardId: vi.fn(), restoreChatSnapshot: () => false,
  loadTexts: vi.fn(), deleteText: vi.fn(), uploadText: vi.fn(), selectText: vi.fn(), error: null, loading: false,
  uploadProgress: null, uploadTaskProgress: null, setCurrentMarketCardId: vi.fn(), setCurrentTextDetailId: vi.fn(),
})

const btn = (name) => screen.getByRole('button', { name })

beforeEach(() => {
  for (const k of Object.keys(mockState)) delete mockState[k]
  Object.assign(mockState, base())
})

describe('D25 第 1 步：只按本任务的（书,角色）禁按钮', () => {
  it('F1 本书祈灼在跑：祈灼灰、江澄可点', () => {
    mockState.distillTasks = [RUNNING('祈灼')]
    mockState.distilling = true
    render(<CharCard />)
    expect(btn('蒸馏中…').disabled).toBe(true)
    expect(btn('蒸馏角色').disabled).toBe(false)
  })

  it('F2 进度大于 0 时写进按钮文字', () => {
    mockState.distillTasks = [RUNNING('祈灼', 't1', 40)]
    mockState.distilling = true
    render(<CharCard />)
    expect(btn('蒸馏中 40%')).toBeTruthy()
  })

  it('F3 别的书的任务不挡本页按钮', () => {
    mockState.distillTasks = [RUNNING('祈灼', 't2')]
    mockState.distilling = true
    render(<CharCard />)
    expect(btn('重新蒸馏').disabled).toBe(false)
  })

  it('F4 本任务已终态：按钮恢复可点', () => {
    mockState.distillTasks = [{ ...RUNNING('祈灼'), status: 'done', done: true, progress_pct: 100 }]
    render(<CharCard />)
    expect(btn('重新蒸馏').disabled).toBe(false)
  })
})
