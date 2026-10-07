import { describe, it, expect, vi } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'
import CharCard from '../CharCard'

// jsdom 缺省能力补齐（useIsMobile / useSwipeBack 依赖）
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
}

const { currentCardBox, setCurrentCard, canWriteBox, moveSpy } = vi.hoisted(() => {
  const box = { card: null, canWrite: true }
  return {
    currentCardBox: () => box.card,
    setCurrentCard: (c) => { box.card = c },
    canWriteBox: { get: () => box.canWrite, set: (v) => { box.canWrite = v } },
    moveSpy: vi.fn(() => Promise.resolve({ ok: true })),
  }
})

vi.mock('../../hooks/useCanWrite', () => ({ default: () => canWriteBox.get() }))

// spec arc-phase-unlocated §6：未定位区只给卡主（可写账号）看、可挪；触发链
// 选阶段 → 点「挪入」→ CardDetail.handleMoveUnlocated → store.moveUnlocated(卡 id, {...})
const ARC = {
  axis: 'a', phases: [{ label: '早', state: 's1' }, { label: '晚', state: 's2' }],
  unlocated: { behaviors: [{ situation: '被揭短', behavior: '涨红脸' }], overlay: {}, attitudes: [] },
}
const card = () => ({
  id: 'c1', name: '测试角色', published_id: null, market_description: '', market_tags: '',
  card_json: JSON.stringify({ name: '测试角色', key_memories: ['记忆一'], character_arc: ARC }),
})

vi.mock('../../store/useAppStore', () => {
  const noop = vi.fn()
  const hook = (sel) => sel({
    currentTextId: 't1',
    texts: [{ id: 't1', filename: 'test.txt' }],
    navigateTo: noop,
    navigateBack: noop,
    currentCard: currentCardBox(),
    cards: [],
    loadCards: noop,
    error: null,
    setError: noop,
    viewCard: noop,
    identifiedChars: [],
    identifying: false,
    distilling: false,
    distillTokenCount: 0,
    distillStatus: '',
    identifyCharacters: noop,
    distillCharacter: noop,
    cardAvatars: {},
    setCardAvatar: noop,
    standaloneCards: [],
    loadStandaloneCards: noop,
    lastDistilledCardId: null,
    setLastDistilledCardId: noop,
    startChat: noop,
    pushView: noop,
    userRolesByCard: {},
    setUserRole: noop,
    getUserRole: noop,
    updateCard: noop,
    moveUnlocated: moveSpy,
  })
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))

// jsdom 无 IndexedDB：store/db 全替身
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(() => Promise.resolve()),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))

vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

describe('CharCard 未定位区', () => {
  it('可写账号：渲染未定位区，挪入调 store.moveUnlocated(卡 id, 分区/序号/阶段/路径)', async () => {
    canWriteBox.set(true)
    setCurrentCard(card())
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-unlocated')).toBeTruthy())
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' })
    fireEvent.click(screen.getByRole('option', { name: '阶段 1 · 早' }))
    fireEvent.click(screen.getByRole('button', { name: '挪入' }))
    await waitFor(() => expect(moveSpy).toHaveBeenCalled())
    expect(moveSpy.mock.calls[0]).toEqual(['c1', { section: 'behaviors', index: 0, phase: 1, path: '',
      expected: { situation: '被揭短', behavior: '涨红脸' } }])
  })

  it('只读账号（游客 / 别人的卡）：不渲染未定位区', async () => {
    canWriteBox.set(false)
    setCurrentCard(card())
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-memory-list')).toBeTruthy())
    expect(container.querySelector('.card-unlocated')).toBeNull()
  })
})
