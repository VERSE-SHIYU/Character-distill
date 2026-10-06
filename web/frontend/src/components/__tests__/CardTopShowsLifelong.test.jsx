import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import CharCard from '../CharCard'

// E15（DA16）：卡片顶部展示「全程成立」的部分 —— 顶层字段照旧渲染，不因阶段 overlay 而改。
// 这是**回归守卫**：本段改的是弧线列表（ArcList），顶部展示不动。基线即绿，红的是实现若误动顶部。
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

const { currentCardBox, setCurrentCard } = vi.hoisted(() => {
  const box = { card: null }
  return { currentCardBox: () => box.card, setCurrentCard: (c) => { box.card = c } }
})

const card = () => ({
  id: 'c1', name: '测试角色', published_id: null, market_description: '', market_tags: '',
  card_json: JSON.stringify({
    name: '测试角色',
    personality_traits: ['全程成立的性格'],
    key_memories: ['记忆一'],
    character_arc: {
      axis: '', phases: [{
        label: '冷', state: '起初',
        overlay: { personality_traits: ['只在此阶段的性格'] },
      }],
    },
  }),
})

vi.mock('../../store/useAppStore', () => {
  const noop = vi.fn()
  const hook = (sel) => sel({
    currentTextId: 't1', texts: [{ id: 't1', filename: 'test.txt' }],
    navigateTo: noop, navigateBack: noop, currentCard: currentCardBox(), cards: [], loadCards: noop,
    error: null, setError: noop, viewCard: noop, identifiedChars: [], identifying: false,
    distilling: false, distillTokenCount: 0, distillStatus: '', identifyCharacters: noop,
    distillCharacter: noop, cardAvatars: {}, setCardAvatar: noop, standaloneCards: [],
    loadStandaloneCards: noop, lastDistilledCardId: null, setLastDistilledCardId: noop,
    startChat: noop, pushView: noop, userRolesByCard: {}, setUserRole: noop, getUserRole: noop,
    updateCard: noop,
  })
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(() => Promise.resolve()), getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

describe('E15 卡片顶部展示全程成立的部分', () => {
  it('顶层 personality_traits 照旧渲染', async () => {
    setCurrentCard(card())
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-arc-list')).toBeTruthy())
    expect(container.textContent).toContain('全程成立的性格')
  })
})
