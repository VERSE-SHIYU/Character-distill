import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import TextPanel from '../TextPanel'
import { fetchWithTimeout } from '../../api/client'

// 角色管理（TextPanel 内层组件）删卡失败要显示在自己身上。
// 内层组件没有外层 TextPanel 的 localError —— 写到外层作用域的变量上就是一个
// ReferenceError，错误既不会显示、还会以未捕获异常的形式丢掉。
// 红源：变异 = 把这句改回外层的 setLocalError，本用例必红。

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

// 两张同名卡 = 分组带版本行：两个 startChat 落点（分组菜单 / 版本行菜单）都要能被点到
const { TEXT_CARDS } = vi.hoisted(() => ({
  TEXT_CARDS: [
    { id: 'card2', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1', created_at: '2026-01-02T00:00:00' },
    { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1', created_at: '2026-01-01T00:00:00' },
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
  vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
    if (opts?.method === 'DELETE') {
      return Promise.reject(Object.assign(new Error('角色卡不存在'), { status: 404 }))
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
  })
  mockState.startChat = () => Promise.resolve()
})

describe('角色管理：删卡失败显示在角色管理里', () => {
  it('DELETE 报错：error-box 显示 detail，卡片还在', async () => {
    const { container } = render(<TextPanel />)
    fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
    await waitFor(() => expect(container.querySelector('.creation-char-menu-btn')).toBeInTheDocument())

    fireEvent.click(container.querySelector('.creation-char-menu-btn'))
    fireEvent.click([...container.querySelectorAll('.creation-char-dropdown button')].find((b) => b.textContent === '删除'))
    fireEvent.click([...document.querySelectorAll('.modal-actions button')].find((b) => b.textContent === '移入回收站'))

    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('角色卡不存在')
  })

  const renderCharacterTab = async () => {
    mockState.startChat = () => Promise.reject(Object.assign(new Error('会话创建失败'), { status: 500 }))
    const { container } = render(<TextPanel />)
    fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
    await waitFor(() => expect(container.querySelector('.creation-char-menu-btn')).toBeInTheDocument())
    return container
  }

  it('分组卡点聊天失败：error-box 显示 detail（原先静默吞掉）', async () => {
    const container = await renderCharacterTab()

    fireEvent.click(container.querySelector('.creation-char-menu-btn'))
    fireEvent.click([...container.querySelectorAll('.creation-char-dropdown button')].find((b) => b.textContent === '聊天'))

    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('会话创建失败')
  })

  it('版本行点聊天失败：error-box 显示 detail（同一个错的另一个落点）', async () => {
    const container = await renderCharacterTab()

    fireEvent.click(container.querySelector('.creation-char-version-badge'))
    const versionMenu = container.querySelector('.creation-char-card-version .creation-char-menu-btn')
    await waitFor(() => expect(versionMenu).toBeInTheDocument())
    fireEvent.click(versionMenu)
    fireEvent.click([...container.querySelectorAll('.creation-char-card-version .creation-char-dropdown button')]
      .find((b) => b.textContent === '聊天'))

    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('会话创建失败')
  })
})
