import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import MarketCardDetail from '../MarketCardDetail'

// 跨区 fork（spec cross-region-fork）：详情页只按后端下发的卡能力渲染。
//   remote —— 只有「使用角色」；赞、评论（含标签页与输入框）、版本 / 衍生、举报、删除都不渲染。
//   local  —— 一切照旧。
//   缺能力 —— 缺省全关（default deny）。
// canWrite 固定 true、查看者设为管理员：被隐藏的按钮是因为能力，而不是因为没权限。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

// 与后端规则表（web/card_visibility.py::CAPABILITIES，后端 test_card_capability_table 锁定）同值
const LOCAL = { fork: true, like: true, comment: true, history: true, report: true, moderate: true }
const REMOTE = { fork: true, like: false, comment: false, history: false, report: false, moderate: false }

const { detailPayload, setDetail } = vi.hoisted(() => {
  const box = { payload: null }
  return { detailPayload: () => box.payload, setDetail: (p) => { box.payload = p } }
})

const detail = (extra) => ({
  id: 'c1', name: '远方角色', visibility: 'public', is_market_card: true,
  user_id: 'u_other', likes: 0, liked_by_me: false,
  card_json: JSON.stringify({ name: '远方角色', key_memories: ['记忆一'] }),
  ...extra,
})

vi.mock('../../hooks/useCanWrite', () => ({ default: () => true }))
vi.mock('../../store/useAppStore', () => {
  const state = {
    currentMarketCardId: 'c1', currentTextId: null,
    authUser: { id: 'u_me', role: 'admin' },
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
  fetchWithTimeout: vi.fn((url) => {
    const body = url.includes('/detail') ? detailPayload() : url.includes('/comments') ? [] : {}
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/EmojiPicker', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

async function renderDetail(payload) {
  setDetail(payload)
  const view = render(<MarketCardDetail />)
  // 独立信号：卡内容网格已渲染，后面的「不存在」断言才不是「什么都没渲染」的假绿
  await waitFor(() => expect(view.container.querySelector('.card-memory-list')).toBeTruthy())
  return view
}

const q = (c) => ({
  use: screen.queryByText('使用角色'),
  like: c.querySelector('.market-detail-like-btn'),
  tabs: c.querySelector('.market-detail-tabs'),
  input: c.querySelector('.market-detail-fixed-input'),
  history: screen.queryByText(/版本历史/),
  report: screen.queryByTitle('举报'),
  remove: screen.queryByTitle('删除'),
})

describe('MarketCardDetail — card capabilities', () => {
  it('remote card: only “use” is offered', async () => {
    const { container } = await renderDetail(detail({ is_remote: true, view: 'remote', capabilities: REMOTE }))
    const x = q(container)
    expect(x.use).toBeTruthy()
    for (const k of ['like', 'tabs', 'input', 'history', 'report', 'remove']) expect(x[k], k).toBeNull()
  })

  it('local card: everything as before', async () => {
    const { container } = await renderDetail(detail({ is_remote: false, view: 'local', capabilities: LOCAL }))
    const x = q(container)
    for (const k of ['use', 'like', 'tabs', 'input', 'history', 'report', 'remove']) expect(x[k], k).toBeTruthy()
  })

  it('history is its own switch: comments without version / fork tabs', async () => {
    const { container } = await renderDetail(detail({ view: 'local', capabilities: { ...LOCAL, history: false } }))
    const x = q(container)
    expect(x.tabs).toBeTruthy()
    expect(x.history).toBeNull()
    expect(screen.queryByText(/衍生角色/)).toBeNull()
  })

  it('missing capabilities: nothing actionable (default deny)', async () => {
    const { container } = await renderDetail(detail({}))
    const x = q(container)
    for (const k of ['use', 'like', 'tabs', 'input', 'report', 'remove']) expect(x[k], k).toBeNull()
  })
})
