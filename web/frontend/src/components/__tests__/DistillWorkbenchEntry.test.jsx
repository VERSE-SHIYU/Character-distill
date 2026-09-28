import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DistillWorkbenchButton from '../common/DistillWorkbenchButton'
import Sidebar from '../Sidebar'
import CharCard from '../CharCard'
import TextPanel from '../TextPanel'

// 蒸馏工作台的三个页面入口：共用按钮（创作→角色、角色管理头部）与侧栏「蒸馏」项。
// 只测接线与高亮；按钮样式复用 .dw-entry-btn，外观由截图判断。

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

describe('蒸馏工作台入口', () => {
  beforeEach(() => { for (const k of Object.keys(mockState)) delete mockState[k]; Object.assign(mockState, base(), { getState: () => mockState }) })

  it('D1 共用按钮：沿用 dw-entry-btn，点击进入 distillWorkbench', () => {
    render(<DistillWorkbenchButton />)
    const btn = screen.getByRole('button', { name: '蒸馏工作台' })
    expect(btn).toHaveClass('dw-entry-btn')
    fireEvent.click(btn)
    expect(mockState.pushView).toHaveBeenCalledWith('distillWorkbench')
  })

  it('D2 侧栏「蒸馏」：点击切到 distillWorkbench', () => {
    render(<Sidebar open pinned={false} onShow={() => {}} onHide={() => {}} onTogglePin={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '蒸馏' }))
    expect(mockState.setView).toHaveBeenCalledWith('distillWorkbench')
  })

  it('D3 侧栏「蒸馏」：在工作台时高亮，在别处不高亮', () => {
    mockState.currentView = 'distillWorkbench'
    const { unmount } = render(<Sidebar open pinned={false} onShow={() => {}} onHide={() => {}} onTogglePin={() => {}} />)
    expect(screen.getByRole('button', { name: '蒸馏' })).toHaveClass('active')
    unmount()
    mockState.currentView = 'home'
    render(<Sidebar open pinned={false} onShow={() => {}} onHide={() => {}} onTogglePin={() => {}} />)
    expect(screen.getByRole('button', { name: '蒸馏' })).not.toHaveClass('active')
  })

  it('D4 角色管理页头部有入口按钮', () => {
    render(<CharCard />)
    expect(screen.getByRole('heading', { name: '角色管理' }).closest('.page-header')).toContainElement(screen.getByRole('button', { name: '蒸馏工作台' }))
  })

  it('D5 创作→角色标签页头部仍有入口按钮', () => {
    render(<TextPanel />)
    fireEvent.click(screen.getByRole('button', { name: '角色管理' }))
    fireEvent.click(screen.getByRole('button', { name: '蒸馏工作台' }))
    expect(mockState.pushView).toHaveBeenCalledWith('distillWorkbench')
  })
})
