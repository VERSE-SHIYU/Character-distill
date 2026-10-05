import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import TextPanel from '../TextPanel'
import { fetchWithTimeout } from '../../api/client'
import {
  validateFile,
  hintFromLimits,
  LIMITS_UNAVAILABLE_MESSAGE,
  resetTextLimitsCache,
} from '../../lib/textLimits'

// 上传上限的唯一来源是后端 /api/text/limits。这组用例锁两件事：
//   1. 前端不写任何兜底数字 —— 提示与校验都由接口返回值生成，接口挂了就禁用；
//   2. 接口失败时不能「放行」（把无上限当有上限）。
// 红源：变异 = 接口失败时把 disabled 判成 false / validateFile 在无 limits 时返回 null，
// 「disables upload when limits fail」「rejects over max_file_bytes」必红。

const LIMITS = { story_max_tokens: 900000, chat_max_chars: 2000000, max_file_bytes: 30 * 1024 * 1024 }

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
vi.mock('../../api/cards', () => ({
  fetchCardsByText: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })),
  fetchStandaloneCards: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })),
  fetchAllCards: vi.fn(() => Promise.resolve([])),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
const { mockState } = vi.hoisted(() => ({
  mockState: {
    authUser: { id: 'u1', role: 'user' },
    texts: [],
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

// 让 fetchWithTimeout('/api/text/limits') 返回 limits；其余 URL 走空响应
function stubLimits(limits) {
  vi.mocked(fetchWithTimeout).mockImplementation((url) => {
    if (url === '/api/text/limits') {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(limits) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
  })
}

function renderPanel() {
  const utils = render(<TextPanel />)
  return utils.container
}

function pickFile(container, file) {
  const input = container.querySelector('.text-upload-input')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
}

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  resetTextLimitsCache()
})

describe('前端上传限制来自接口，不是写死的数字', () => {
  it('rejects over max_file_bytes', () => {
    const tooBig = { name: 'a.txt', size: LIMITS.max_file_bytes + 1 }
    const atLimit = { name: 'a.txt', size: LIMITS.max_file_bytes }
    expect(validateFile(tooBig, LIMITS)).toBeTruthy()
    expect(validateFile(atLimit, LIMITS)).toBeNull()
    // 取不到限制时一律拒（不能把「不知道」当「没上限」）
    expect(validateFile(atLimit, null)).toBe(LIMITS_UNAVAILABLE_MESSAGE)
  })

  it('shows size message', async () => {
    stubLimits({ ...LIMITS, max_file_bytes: 1 * 1024 * 1024 })
    const container = renderPanel()
    await waitFor(() => expect(container.querySelector('.text-upload-meta').textContent).not.toContain(LIMITS_UNAVAILABLE_MESSAGE))

    pickFile(container, { name: 'a.txt', size: 2 * 1024 * 1024 })
    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('1MB 上限')
  })

  it('disables upload when limits fail', async () => {
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      if (url === '/api/text/limits') return Promise.reject(new Error('network'))
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) })
    })
    const container = renderPanel()
    const btn = [...container.querySelectorAll('.btn-primary')].find((b) => b.textContent.includes('选择文件上传'))

    await waitFor(() => expect(container.querySelector('.text-upload-meta').textContent).toContain(LIMITS_UNAVAILABLE_MESSAGE))
    expect(btn).toBeDisabled()
  })

  it('hint built from limits', () => {
    // 故意用非默认值：写死的提示文案不会跟着变，这个断言就会红
    const hint = hintFromLimits({ story_max_tokens: 500000, chat_max_chars: 300000, max_file_bytes: 5 * 1024 * 1024 })
    expect(hint).toContain('50 万 tokens')
    expect(hint).toContain('30 万字')
    expect(hint).toContain('5MB')
  })
})
