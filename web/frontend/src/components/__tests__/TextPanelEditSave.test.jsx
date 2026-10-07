import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import TextPanel from '../TextPanel'
import { fetchWithTimeout } from '../../api/client'

// 角色管理（TextPanel 内层组件）的编辑保存：走 store 的唯一 PATCH 出口 updateCard，并带上
// 这张卡出卡时的 revision（乐观锁，后端 spec arc-phase-unlocated §13）。
// 原先这里自己发 `PUT /api/distill/card/{id}`，后端只有 PATCH → 必 405，编辑从来存不上。
// 红源：变异 = 改回自己发 PUT，本用例必红（updateCard 没被调）。

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

const { TEXT_CARDS } = vi.hoisted(() => ({
  TEXT_CARDS: [
    { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1', created_at: '2026-01-01T00:00:00', revision: 'rev-1' },
  ],
}))

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../../api/cards', () => ({
  fetchCardsByText: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(TEXT_CARDS) })),
  fetchStandaloneCards: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })),
  fetchAllCards: vi.fn(() => Promise.resolve(TEXT_CARDS)),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
const { mockState } = vi.hoisted(() => ({
  mockState: {
    authUser: { id: 'u1', role: 'user' },
    texts: [{ id: 't1', title: '文本甲', filename: 'a.txt', char_count: 100 }],
    loading: false,
    error: null,
    loadTexts: () => {},
    uploadText: () => {},
    uploadProgress: null,
    uploadTaskProgress: {},
    deleteText: () => {},
    selectText: () => {},
    currentTextId: null,
    setCurrentTextDetailId: () => {},
    navigateTo: () => {},
    pushView: () => {},
    setCurrentMarketCardId: () => {},
    startChat: () => Promise.resolve(),
    updateCard: vi.fn(() => Promise.resolve({ ok: true })),
  },
}))

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = () => {}
  return { default: hook }
})

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve([]) })
  mockState.updateCard.mockClear()
})

async function openEdit(container) {
  fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
  await waitFor(() => expect(container.querySelector('.creation-char-menu-btn')).toBeInTheDocument())
  fireEvent.click(container.querySelector('.creation-char-menu-btn'))
  fireEvent.click([...container.querySelectorAll('.creation-char-dropdown button')].find((b) => b.textContent === '编辑'))
  await waitFor(() => expect(document.querySelector('.edit-card-modal')).toBeInTheDocument())
}

const saveButton = () => [...document.querySelectorAll('.edit-card-modal button')].find((b) => b.textContent === '保存')

describe('角色管理：编辑保存', () => {
  // spec §13.3（补充 12）：这里用真的 EditCardModal —— 409 时弹窗内报错、弹窗不关、用户改的
  // 内容还在；成功才关。两侧都测。
  it('保存失败（409）：弹窗不关，报错上屏，用户改的内容还在', async () => {
    mockState.updateCard.mockRejectedValueOnce(new Error('这张卡已在别处更新，请刷新后再改'))
    const { container } = render(<TextPanel />)
    await openEdit(container)
    const traits = document.querySelector('.edit-card-modal textarea.modal-textarea')
    fireEvent.change(traits, { target: { value: '我刚改的一行' } })
    fireEvent.click(saveButton())

    await waitFor(() => expect(document.querySelector('.edit-card-modal .error-box')).toBeInTheDocument())
    expect(document.querySelector('.edit-card-modal .error-box').textContent).toContain('这张卡已在别处更新，请刷新后再改')
    expect(document.querySelector('.edit-card-modal textarea.modal-textarea').value).toBe('我刚改的一行')
    expect(mockState.updateCard).toHaveBeenCalledTimes(1)
  })

  it('保存成功：弹窗关闭', async () => {
    const { container } = render(<TextPanel />)
    await openEdit(container)
    fireEvent.click(saveButton())
    await waitFor(() => expect(mockState.updateCard).toHaveBeenCalled())
    await waitFor(() => expect(document.querySelector('.edit-card-modal')).toBeNull())
  })

  it('保存走 store.updateCard(卡 id, 卡内容, revision)，不自己发请求', async () => {
    const { container } = render(<TextPanel />)
    fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
    await waitFor(() => expect(container.querySelector('.creation-char-menu-btn')).toBeInTheDocument())

    fireEvent.click(container.querySelector('.creation-char-menu-btn'))
    fireEvent.click([...container.querySelectorAll('.creation-char-dropdown button')].find((b) => b.textContent === '编辑'))
    await waitFor(() => expect([...document.querySelectorAll('button')].some((b) => b.textContent === '保存')).toBe(true))
    fireEvent.click([...document.querySelectorAll('button')].find((b) => b.textContent === '保存'))

    await waitFor(() => expect(mockState.updateCard).toHaveBeenCalled())
    const [id, , revision] = mockState.updateCard.mock.calls[0]
    expect([id, revision]).toEqual(['card1', 'rev-1'])
    expect(vi.mocked(fetchWithTimeout).mock.calls.filter(([u, o]) => u.startsWith('/api/distill/card/') && o?.method)).toEqual([])
  })
})
