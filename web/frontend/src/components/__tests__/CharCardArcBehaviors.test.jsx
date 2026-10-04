import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
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

const ARC = ['从冷漠到学会信任', '从逃避责任到主动担当']

// 新卡形态：变化轴 + 带心态名的阶段；情境→行为条目
const NEW_ARC = { axis: '从桀骜到担当', phases: [{ label: '桀骜不服', state: '大闹天宫前后' }] }
const BEHAVIORS = [{ situation: '被人当众质疑', behavior: '先反问对方凭什么', source_quote: '你凭什么这么说' }]

const { currentCardBox, setCurrentCard } = vi.hoisted(() => {
  const box = { card: null }
  return {
    currentCardBox: () => box.card,
    setCurrentCard: (c) => { box.card = c },
  }
})

// 有弧线 / 无弧线两版卡；key_memories 两版都有，用作「卡片确实渲染了」的独立信号
const card = (arc, extra = {}) => ({
  id: 'c1',
  name: '测试角色',
  published_id: null,
  market_description: '',
  market_tags: '',
  card_json: JSON.stringify({
    name: '测试角色',
    key_memories: ['记忆一'],
    ...(arc ? { character_arc: arc } : {}),
    ...extra,
  }),
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

describe('CharCard 角色弧线展示', () => {
  it('有 character_arc：详情页按 card_json 顺序渲染', async () => {
    setCurrentCard(card(ARC))
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-arc-list')).toBeTruthy())
    const texts = [...container.querySelectorAll('.card-arc-item .card-arc-text')].map((el) => el.textContent)
    expect(texts).toEqual(ARC)
  })

  it('新卡弧线：显示变化轴，阶段显示「心态 · 状态」', async () => {
    setCurrentCard(card(NEW_ARC))
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-arc-list')).toBeTruthy())
    expect(container.querySelector('.card-arc-axis').textContent).toBe('从桀骜到担当')
    expect(container.querySelector('.card-arc-item .card-arc-text').textContent).toBe('桀骜不服 · 大闹天宫前后')
  })

  it('有 situation_behaviors：渲染情境、做法与原文摘录', async () => {
    setCurrentCard(card(null, { situation_behaviors: BEHAVIORS }))
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-behavior-list')).toBeTruthy())
    expect(container.querySelector('.card-behavior-situation').textContent).toBe('被人当众质疑')
    expect(container.querySelector('.card-behavior-text').textContent).toBe('先反问对方凭什么')
    expect(container.querySelector('.card-behavior-quote').textContent).toBe('你凭什么这么说')
  })

  it('无 situation_behaviors：整节不渲染', async () => {
    setCurrentCard(card(null))
    const { container } = render(<CharCard />)
    await waitFor(() => expect(container.querySelector('.card-memory-list')).toBeTruthy())
    expect(container.querySelector('.card-behavior-list')).toBeNull()
  })

  it('无 character_arc：整节不渲染', async () => {
    setCurrentCard(card(null))
    const { container } = render(<CharCard />)
    // 独立信号：先等同节另一字段出现，证明卡片已渲染 ——
    // 否则下面的 null 只是「什么都没渲染」的假绿（§四「用例绿 ≠ 命题成立」）
    await waitFor(() => expect(container.querySelector('.card-memory-list')).toBeTruthy())
    expect(container.querySelector('.card-arc-list')).toBeNull()
  })
})
