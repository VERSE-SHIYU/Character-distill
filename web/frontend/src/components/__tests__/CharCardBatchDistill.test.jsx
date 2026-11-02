import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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
  texts: [{ id: 't1', filename: 'a.md' }], cards: [{ id: 'c2', name: '乙', card_json: '{"name":"乙"}', text_id: 't1' }], cardAvatars: {}, userRolesByCard: {},
  currentCard: null, sessionId: null, unreadTotal: 0,
  setView: vi.fn(), pushView: vi.fn(), navigateTo: vi.fn(), navigateBack: vi.fn(), popView: vi.fn(),
  startChat: vi.fn(), logout: vi.fn(), setError: vi.fn(), setCardAvatar: vi.fn(),
  getUserRole: () => '', setUserRole: vi.fn(), updateCard: vi.fn(), viewCard: vi.fn(), loadCards: vi.fn(),
  identifiedChars: [{ name: '甲' }, { name: '乙' }, { name: '丙' }], identifying: false,
  distilling: false, distillTokenCount: 0, distillStatus: '', distillTasks: [],
  identifyCharacters: vi.fn(), distillCharacter: vi.fn(() => Promise.resolve(true)), standaloneCards: [], loadStandaloneCards: vi.fn(),
  distillBatch: vi.fn(() => Promise.resolve(1)),
  lastDistilledCardId: null, setLastDistilledCardId: vi.fn(), restoreChatSnapshot: () => false,
  loadTexts: vi.fn(), deleteText: vi.fn(), uploadText: vi.fn(), selectText: vi.fn(), error: null, loading: false,
  uploadProgress: null, uploadTaskProgress: null, setCurrentMarketCardId: vi.fn(), setCurrentTextDetailId: vi.fn(),
})

const btn = (name) => screen.getByRole('button', { name })
const cb = (name) => screen.getByRole('checkbox', { name: `选择 ${name}` })

beforeEach(() => {
  for (const k of Object.keys(mockState)) delete mockState[k]
  Object.assign(mockState, base())
})

describe('D25 第 3 步：勾选一键蒸', () => {
  it.fails('U1 乙已蒸过：甲、丙可勾，乙禁用', () => {
    render(<CharCard />)
    expect(cb('甲').disabled).toBe(false)
    expect(cb('丙').disabled).toBe(false)
    expect(cb('乙').disabled).toBe(true)
  })

  it.fails('U2 勾甲、丙后点「蒸馏所选（2）」调 distillBatch', async () => {
    render(<CharCard />)
    fireEvent.click(cb('甲'))
    fireEvent.click(cb('丙'))
    fireEvent.click(btn('蒸馏所选（2）'))
    await waitFor(() => expect(mockState.distillBatch).toHaveBeenCalledWith('t1', ['甲', '丙']))
  })

  it.fails('U3 甲有 running 任务：甲的勾选框禁用', () => {
    mockState.distillTasks = [RUNNING('甲')]
    render(<CharCard />)
    expect(cb('甲').disabled).toBe(true)
  })

  it.fails('U4 勾甲后甲进入 running：显示未勾，N 为 0 且禁用', () => {
    const { rerender } = render(<CharCard />)
    fireEvent.click(cb('甲'))
    expect(cb('甲').checked).toBe(true)
    mockState.distillTasks = [RUNNING('甲')]
    rerender(<CharCard />)
    expect(cb('甲').checked).toBe(false)
    expect(btn('蒸馏所选（0）').disabled).toBe(true)
  })

  it.fails('U5 queued 任务：单个按钮文字「排队中」', () => {
    mockState.distillTasks = [{ ...RUNNING('甲'), status: 'queued', done: false }]
    render(<CharCard />)
    expect(btn('排队中').disabled).toBe(true)
  })

  it('U6 running 40%：文字「蒸馏中 40%」（对照）', () => {
    mockState.distillTasks = [RUNNING('甲', 't1', 40)]
    render(<CharCard />)
    expect(btn('蒸馏中 40%')).toBeTruthy()
  })
})
