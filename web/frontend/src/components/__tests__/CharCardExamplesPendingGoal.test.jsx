/**
 * 「待补对话示例」目标检查 · 角色页（docs/specs/examples-pending.md §1）。
 *
 * 定好的目标（Shiyu 2026-10-10）：蒸馏出来的卡缺对话示例，打开这张卡时自动弹出编辑页让用户
 * 自己填；填不出来可以让系统重新找一次；提醒只针对新蒸馏出来的卡，只有这一次。
 *
 * 走真实的 CharCard 与真实的 EditCardModal，只把 store 换成可控的假状态（store 自己的行为
 * 在 `store/examplesPending.test.js`）。
 *
 * C1 打开一张「待补」的卡 → 编辑页自己弹出，里面有「重新找一次」
 * C2 不是「待补」的卡（含没有这个字段的旧卡）→ 不弹
 * C3 关掉弹窗 → 告诉服务端「关掉了」（一次，带这张卡的卡号）；标记清掉后不再弹
 * C4 在弹出的编辑页里保存 → 按这张卡打开时的卡号和版本号提交，不再另报「关掉了」
 * C5 点「重新找一次」→ 用这张卡的卡号和版本号去找
 * C6 只读账号（游客）→ 不弹
 * C7 编辑页开着时页面换到另一张卡 → 编辑页关掉，不会把这张卡的内容写到那张卡上
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'
import CharCard from '../CharCard'

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null,
      dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

const { box, updateSpy, refindSpy, dismissSpy } = vi.hoisted(() => ({
  box: { card: null, canWrite: true },
  updateSpy: vi.fn(() => Promise.resolve({ ok: true })),
  refindSpy: vi.fn(() => Promise.resolve(true)),
  dismissSpy: vi.fn(() => Promise.resolve()),
}))

vi.mock('../../hooks/useCanWrite', () => ({ default: () => box.canWrite }))

vi.mock('../../store/useAppStore', () => {
  const noop = vi.fn()
  const hook = (sel) => sel({
    currentTextId: 't1',
    texts: [{ id: 't1', filename: 'test.txt' }],
    navigateTo: noop, navigateBack: noop,
    currentCard: box.card,
    cards: [],
    loadCards: noop,
    error: null, setError: noop,
    viewCard: noop,
    identifiedChars: [], identifying: false, distilling: false, distillTokenCount: 0, distillStatus: '',
    identifyCharacters: noop, distillCharacter: noop,
    cardAvatars: {}, setCardAvatar: noop,
    standaloneCards: [], loadStandaloneCards: noop,
    lastDistilledCardId: null, setLastDistilledCardId: noop,
    startChat: noop, pushView: noop,
    userRolesByCard: {}, setUserRole: noop, getUserRole: noop,
    updateCard: updateSpy,
    moveUnlocated: noop,
    refindExamples: refindSpy,
    dismissExamplesPending: dismissSpy,
  })
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(() => Promise.resolve()),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

const card = (over = {}) => ({
  id: 'c1', name: '角色甲', published_id: null, market_description: '', market_tags: '', revision: 'r1',
  card_json: JSON.stringify({ name: '角色甲', identity: '甲的身份' }),
  ...over,
})
const modal = () => document.querySelector('.edit-card-modal')
const inModal = (text) => [...modal().querySelectorAll('button')].find((b) => b.textContent === text)

beforeEach(() => {
  box.canWrite = true
  updateSpy.mockClear()
  refindSpy.mockClear()
  dismissSpy.mockClear()
})

describe('角色页：新蒸馏的卡缺对话示例', () => {
  it('C1 打开一张「待补」的卡 → 编辑页自己弹出，里面有「重新找一次」', async () => {
    box.card = card({ examples_pending_for: '角色甲' })
    render(<CharCard />)

    await waitFor(() => expect(modal()).toBeInTheDocument())
    expect(inModal('重新找一次')).toBeTruthy()
  })

  it('C2 不是「待补」的卡（含没有这个字段的旧卡）→ 不弹', async () => {
    box.card = card({ examples_pending_for: null })
    const { unmount } = render(<CharCard />)
    await screen.findByRole('button', { name: /编辑/ })
    expect(modal()).toBeNull()
    unmount()

    box.card = card()
    render(<CharCard />)
    await screen.findByRole('button', { name: /编辑/ })
    expect(modal()).toBeNull()
  })

  it('C3 关掉弹窗 → 告诉服务端一次；标记清掉后不再弹', async () => {
    box.card = card({ examples_pending_for: '角色甲' })
    const { rerender } = render(<CharCard />)
    await waitFor(() => expect(modal()).toBeInTheDocument())

    fireEvent.click(inModal('取消'))

    await waitFor(() => expect(modal()).toBeNull())
    expect(dismissSpy.mock.calls).toEqual([['c1']])
    box.card = card({ examples_pending_for: null })          // store 清掉了标记
    rerender(<CharCard />)
    expect(modal()).toBeNull()
  })

  it('C4 在弹出的编辑页里保存 → 按打开时的卡号和版本号提交，不另报「关掉了」', async () => {
    box.card = card({ examples_pending_for: '角色甲' })
    render(<CharCard />)
    await waitFor(() => expect(modal()).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText(/对方：xxx/), { target: { value: '路人：你好。\n角色甲：我自己填的。' } })

    fireEvent.click(inModal('保存'))

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1))
    const [cardId, saved, revision] = updateSpy.mock.calls[0]
    expect([cardId, revision]).toEqual(['c1', 'r1'])
    expect(saved.dialogue_examples).toEqual(['路人：你好。\n角色甲：我自己填的。'])
    await waitFor(() => expect(modal()).toBeNull())
    expect(dismissSpy).not.toHaveBeenCalled()
  })

  it('C5 点「重新找一次」→ 用这张卡的卡号和版本号去找', async () => {
    box.card = card({ examples_pending_for: '角色甲' })
    render(<CharCard />)
    await waitFor(() => expect(modal()).toBeInTheDocument())

    fireEvent.click(inModal('重新找一次'))

    await waitFor(() => expect(refindSpy.mock.calls).toEqual([['c1', 'r1']]))
  })

  it('C6 只读账号（游客）→ 不弹', async () => {
    box.canWrite = false
    box.card = card({ examples_pending_for: '角色甲' })
    const { container } = render(<CharCard />)

    await waitFor(() => expect(container.querySelector('.card-detail')).toBeTruthy())
    expect(modal()).toBeNull()
    expect(dismissSpy).not.toHaveBeenCalled()
  })

  it('C7 编辑页开着时页面换到另一张卡 → 编辑页关掉', async () => {
    box.card = card()
    const { rerender } = render(<CharCard />)
    fireEvent.click(await screen.findByRole('button', { name: /编辑/ }))
    await waitFor(() => expect(modal()).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('甲的身份'), { target: { value: '甲改到一半' } })

    box.card = card({ id: 'c2', name: '角色乙', revision: 'r9', card_json: JSON.stringify({ name: '角色乙', identity: '乙的身份' }) })
    rerender(<CharCard />)

    expect(modal()).toBeNull()
    expect(updateSpy).not.toHaveBeenCalled()
  })
})
