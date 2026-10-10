/**
 * 广场卡编辑保存带版本号（docs/specs/examples-pending.md §1 第四部分）。
 *
 * 编辑一张自己发布的卡再保存：请求里带上编辑页打开时这张卡的版本号，后端据此发现「卡在别处
 * 变过」。恢复版本是有意的整张覆盖，不带。
 *
 * K1 编辑保存的请求带 revision，值是编辑页打开时这张卡的版本号
 *（恢复版本不带版本号、照旧成功，由后端的 L3 守。）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'
import MarketCardDetail from '../MarketCardDetail'
import { fetchWithTimeout } from '../../api/client'

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

const CARD = {
  id: 'c1', name: '测试角色', visibility: 'public', user_id: 'u_me', likes: 0, liked_by_me: false,
  revision: 'rev-9', market_description: '', market_tags: '',
  view: 'local', capabilities: { report: true, fork: true, comment: true, like: true, chat: true },
  card_json: JSON.stringify({ name: '测试角色', key_memories: ['记忆一'] }),
}

vi.mock('../../store/useAppStore', () => {
  const state = {
    currentMarketCardId: 'c1', currentTextId: null, authUser: { id: 'u_me', role: 'user' },
    setView: vi.fn(), pushView: vi.fn(), navigateTo: vi.fn(), navigateBack: vi.fn(),
    setAuthorUserId: vi.fn(), setMessageTargetUserId: vi.fn(), setMessageTargetUsername: vi.fn(),
    startChat: vi.fn(), loadStandaloneCards: vi.fn(), setCurrentMarketCardId: vi.fn(),
  }
  const hook = (sel) => sel(state)
  hook.getState = () => state
  hook.setState = (patch) => Object.assign(state, patch)
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
}))

// 真弹窗保存时交回：卡的内容，和它打开时那张卡的卡号与版本号。
vi.mock('../EditCardModal', () => ({
  default: ({ isOpen, onSave, cardId, revision }) => (isOpen
    ? <button onClick={() => onSave({ name: '改' }, { cardId, revision })}>假保存</button>
    : null),
}))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/EmojiPicker', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
const publishCalls = () => vi.mocked(fetchWithTimeout).mock.calls
  .filter(([url, opts]) => url === '/api/market/c1/publish' && opts?.method === 'PUT')
  .map(([, opts]) => JSON.parse(opts.body))

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockImplementation((url) => {
    if (url === '/api/cards/c1/detail' || url === '/api/market/card/c1') return ok(CARD)
    return ok({})
  })
})

describe('广场卡：编辑保存带版本号', () => {
  it('K1 编辑保存的请求带 revision，值是编辑页打开时这张卡的版本号', async () => {
    render(<MarketCardDetail />)
    fireEvent.click(await screen.findByTitle('编辑'))

    fireEvent.click(await screen.findByRole('button', { name: '假保存' }))

    await waitFor(() => expect(publishCalls()).toHaveLength(1))
    expect(publishCalls()[0].revision).toBe('rev-9')
    expect(JSON.parse(publishCalls()[0].card_json)).toEqual({ name: '改' })
  })
})
