import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import CharCard from '../CharCard'
import { fetchWithTimeout } from '../../api/client'

// 105：发布到市场失败时，后端 detail 必须显示在弹窗里，弹窗不关、状态不置为「已发布」。
// 两条各守一个分支：接口抛错（403/500）与 200 但响应没有 card_id。

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

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

const { mockState } = vi.hoisted(() => ({
  mockState: {
    authUser: { id: 'u1', role: 'user' },
    currentView: 'text',
    currentTextId: 't1',
    texts: [{ id: 't1', filename: 'a.txt' }],
    cards: [],
    cardAvatars: {},
    userRolesByCard: {},
    navigateTo: () => {},
    navigateBack: () => {},
    setError: () => {},
    setCardAvatar: () => {},
    startChat: () => {},
    pushView: () => {},
    getState: () => mockState,
    getUserRole: () => '',
    setUserRole: () => {},
    updateCard: () => {},
    viewCard: () => {},
    loadCards: () => {},
    identifiedChars: [],
    identifying: false,
    distilling: false,
    distillTokenCount: 0,
    distillStatus: '',
    identifyCharacters: () => {},
    distillCharacter: () => {},
    standaloneCards: [],
    loadStandaloneCards: () => {},
    lastDistilledCardId: null,
    setLastDistilledCardId: () => {},
    restoreChatSnapshot: () => false,
  },
}))

vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

const CARD = { id: 'c1', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1', published_id: null }

const openPublishModal = async () => {
  mockState.cards = [CARD]
  mockState.currentCard = CARD
  render(<CharCard />)
  await waitFor(() => expect(document.querySelector('#card-share-btn')).toBeInTheDocument())
  fireEvent.click(document.querySelector('#card-share-btn'))
  // 两个 .publish-textarea：第 0 个是「角色描述」，第 1 个是「发布说明」。
  // 发布说明为空时确认按钮是 disabled，点了不会发请求。
  fireEvent.change(document.querySelectorAll('.publish-textarea')[1], { target: { value: '首版说明' } })
}

const publish = async () => {
  const confirm = [...document.querySelectorAll('.modal-actions button')].find((b) => b.textContent.includes('发布'))
  fireEvent.click(confirm)
  await waitFor(() => expect(document.querySelector('.error-box')).toBeInTheDocument())
}

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
})

describe('105：发布失败的文案要落到弹窗里', () => {
  it('接口抛错（403）：显示 detail，弹窗仍在，没变成「已发布」', async () => {
    await openPublishModal()
    // fetchWithTimeout 失败时抛的就是带 detail 的 AppError，这里最小化复现
    vi.mocked(fetchWithTimeout).mockImplementationOnce(() =>
      Promise.reject(Object.assign(new Error('游客无权发布到市场'), { status: 403 })))

    await publish()
    expect(document.querySelector('.error-box').textContent).toContain('游客无权发布到市场')
    expect(document.querySelector('.publish-form-body')).toBeInTheDocument()
    expect(document.querySelector('#card-share-btn').textContent).toContain('分享到市场')
  })

  it('200 但没有 card_id：同样显示文案，弹窗仍在，没变成「已发布」', async () => {
    await openPublishModal()
    vi.mocked(fetchWithTimeout).mockImplementationOnce(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }))

    await publish()
    expect(document.querySelector('.error-box').textContent).toContain('card_id')
    expect(document.querySelector('.publish-form-body')).toBeInTheDocument()
    expect(document.querySelector('#card-share-btn').textContent).toContain('分享到市场')
  })

  it('成功（拿到 card_id）：弹窗关闭，按钮变成「已分享」', async () => {
    await openPublishModal()
    vi.mocked(fetchWithTimeout).mockImplementationOnce(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ card_id: 'pub1' }) }))

    const confirm = [...document.querySelectorAll('.modal-actions button')].find((b) => b.textContent.includes('发布'))
    fireEvent.click(confirm)
    await waitFor(() => expect(document.querySelector('.publish-form-body')).toBeNull())
    expect(document.querySelector('#card-share-btn').textContent).toContain('已分享')
  })
})
