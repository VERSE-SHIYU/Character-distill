import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'
import MarketCardDetail from '../MarketCardDetail'

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

const { detailPayload, setDetail } = vi.hoisted(() => {
  const box = { payload: null }
  return {
    detailPayload: () => box.payload,
    setDetail: (p) => { box.payload = p },
  }
})

// 有弧线 / 无弧线两版载荷；key_memories 两版都有，用作「网格确实渲染了」的独立信号
const detail = (arc) => ({
  id: 'c1',
  name: '测试角色',
  visibility: 'private',
  user_id: 'u_other',
  likes: 0,
  liked_by_me: false,
  card_json: JSON.stringify({
    name: '测试角色',
    key_memories: ['记忆一'],
    ...(arc ? { character_arc: arc } : {}),
  }),
})

vi.mock('../../store/useAppStore', () => {
  const state = {
    currentMarketCardId: 'c1',
    currentTextId: null,
    authUser: { id: 'u_me' },
    setView: vi.fn(),
    pushView: vi.fn(),
    navigateTo: vi.fn(),
    navigateBack: vi.fn(),
    setAuthorUserId: vi.fn(),
    setMessageTargetUserId: vi.fn(),
    setMessageTargetUsername: vi.fn(),
    startChat: vi.fn(),
    loadStandaloneCards: vi.fn(),
  }
  const hook = (sel) => sel(state)
  hook.getState = () => state
  hook.setState = (patch) => Object.assign(state, patch)
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url) => {
    if (url.includes('/detail')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(detailPayload()) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
  }),
  getAuthHeaders: vi.fn(() => ({})),
}))

// 重子件不参与本轮命题
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/EmojiPicker', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))

describe('MarketCardDetail 角色弧线展示', () => {
  it('有 character_arc：详情页按 card_json 顺序渲染', async () => {
    setDetail(detail(ARC))
    const { container } = render(<MarketCardDetail />)
    await waitFor(() => expect(container.querySelector('.card-arc-list')).toBeTruthy())
    const texts = [...container.querySelectorAll('.card-arc-item .card-arc-text')].map((el) => el.textContent)
    expect(texts).toEqual(ARC)
  })

  it('无 character_arc：整节不渲染', async () => {
    setDetail(detail(null))
    const { container } = render(<MarketCardDetail />)
    // 独立信号：先等同节另一字段出现，证明网格已渲染 ——
    // 否则下面的 null 只是「什么都没渲染」的假绿（§四「用例绿 ≠ 命题成立」）
    await waitFor(() => expect(container.querySelector('.card-memory-list')).toBeTruthy())
    expect(container.querySelector('.card-arc-list')).toBeNull()
  })
})

describe('card-arc-* 样式只落一处', () => {
  it('三条类名定义在 global.css，且不出现在 adm-theme.css', () => {
    const read = (p) => fs.readFileSync(path.join(__dirname, p), 'utf8')
    const global = read('../../styles/global.css')
    for (const cls of ['.card-arc-list', '.card-arc-item', '.card-arc-index']) {
      expect(global).toMatch(new RegExp(`\\${cls}\\s*\\{`))
    }
    expect(read('../../styles/adm-theme.css')).not.toMatch(/card-arc-/)
  })
})

describe('card-arc-text 长串溢出防护', () => {
  it('无空格长拉丁串完整落入 card-arc-text（未被截断）', async () => {
    const LONG = `https://example.com/${'a'.repeat(120)}`
    setDetail(detail([LONG]))
    const { container } = render(<MarketCardDetail />)
    await waitFor(() => expect(container.querySelector('.card-arc-text')).toBeTruthy())
    // 只证「串完整到达 DOM」——截断（如 s.slice(0, N)）会被这条抓住。
    // 溢出本身在 jsdom 里测不了，见下一条。
    expect(container.querySelector('.card-arc-text').textContent).toBe(LONG)
  })

  it('.card-arc-text 声明 min-width: 0（源级断言，替代测不了的溢出断言）', () => {
    // jsdom 不做布局：scrollWidth / offsetWidth 恒为 0，写「不溢出」的像素断言是**恒真假绿**，
    // 正是 §四「对空集/无布局的断言恒真」那类。能红的只有「消除溢出的那条声明确实在」。
    const css = fs.readFileSync(path.join(__dirname, '../../styles/global.css'), 'utf8')
    const block = css.match(/\.card-arc-text\s*\{([^}]*)\}/)
    expect(block).toBeTruthy()
    expect(block[1]).toMatch(/min-width\s*:\s*0/)
  })
})
