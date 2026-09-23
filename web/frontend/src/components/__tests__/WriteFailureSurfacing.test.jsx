import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import PostCard from '../common/PostCard'
import HistoryPanel from '../HistoryPanel'
import EditCardModal from '../EditCardModal'
import { fetchWithTimeout } from '../../api/client'

// 写失败必须让用户看见 —— 三种落点各一条：
//   1. 普通落点：失败就 setError，ErrorBox 显示在动作发生的组件里
//   2. 乐观更新落点：失败要回滚，只有真正成功的才从列表里拿掉
//   3. 弹窗落点：错误显示在弹窗里，弹窗不关（用户改的内容不能丢）
//
// fetchWithTimeout 失败时抛的就是带 detail 的 AppError，这里最小化复现。

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

const appError = (message, status = 500) => Object.assign(new Error(message), { status })

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../../store/db', () => ({
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))

const { mockState } = vi.hoisted(() => ({
  mockState: {
    authUser: { id: 'u1', role: 'user' },
    texts: [],
    textProgress: {},
    loadTextProgress: () => {},
    cards: [],
    cardAvatars: {},
    setCardAvatar: () => {},
    resumeSession: () => {},
    resumeLoading: false,
    popView: () => {},
    pushView: () => {},
    navigateTo: () => {},
    setResumeGroupId: () => {},
    setAuthorUserId: () => {},
    setCurrentMarketCardId: () => {},
    getState: () => mockState,
  },
}))

vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
})

describe('写失败要显示在动作发生的组件里', () => {
  const POST = { id: 'p1', content: '你好', user_id: 'u1', author_name: '甲', likes: 2, comment_count: 0 }

  it('动态评论发送失败：文案可见，评论框内容不丢', async () => {
    vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
      if (opts?.method === 'POST') return Promise.reject(appError('评论失败，请稍后再试'))
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ comments: [] }) })
    })
    const { container } = render(<PostCard post={POST} />)

    fireEvent.click(container.querySelectorAll('.post-card-action-btn')[1])
    await waitFor(() => expect(container.querySelector('.post-card-comment-input')).toBeInTheDocument())
    fireEvent.change(container.querySelector('.post-card-comment-input'), { target: { value: '说点什么' } })
    fireEvent.click(container.querySelector('.post-card-comment-send'))

    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('评论失败，请稍后再试')
    expect(container.querySelector('.post-card-comment-input').value).toBe('说点什么')
  })
})

describe('乐观更新失败要回滚', () => {
  const A = { id: 'a1', character_name: '甲', text_id: 't1', last_message: 'hi', updated_at: '2026-01-01T00:00:00Z', card_id: 'c1' }
  const B = { id: 'b1', character_name: '乙', text_id: 't1', last_message: 'yo', updated_at: '2026-01-02T00:00:00Z', card_id: 'c2' }

  const renderList = async () => {
    vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
      if (opts?.method === 'DELETE') {
        return url.endsWith('/a1')
          ? Promise.resolve({ ok: true, status: 200 })
          : Promise.reject(appError('删除失败，请稍后再试'))
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ items: [A, B], total: 2 }) })
    })
    const { container } = render(<HistoryPanel />)
    await waitFor(() => expect(container.querySelectorAll('.history-item').length).toBe(2))
    return container
  }

  it('批量删除部分失败：成功的走掉，失败的原位留着并报错', async () => {
    const container = await renderList()

    fireEvent.click([...container.querySelectorAll('button')].find((b) => b.textContent.trim() === '多选'))
    const boxes = container.querySelectorAll('.history-checkbox')
    expect(boxes.length).toBe(2)
    fireEvent.click(boxes[0])
    fireEvent.click(boxes[1])

    fireEvent.click([...container.querySelectorAll('button')].find((b) => b.textContent.includes('移入回收站 (')))
    // ConfirmModal 走 portal，落在 document.body 上
    fireEvent.click([...document.querySelectorAll('.modal-actions button')].find((b) => b.textContent.trim() === '删除'))

    await waitFor(() => expect(container.querySelector('.error-box')).toBeInTheDocument())
    expect(container.querySelector('.error-box').textContent).toContain('删除失败，请稍后再试')

    const names = [...container.querySelectorAll('.history-item-name')].map((el) => el.textContent)
    expect(names).toEqual(['乙'])
  })
})

describe('弹窗里的写失败不关窗', () => {
  it('保存失败：错误显示在弹窗内，弹窗还开着、按钮可再点', async () => {
    const onSave = vi.fn(() => Promise.reject(appError('保存失败，请稍后再试')))
    render(<EditCardModal isOpen data={{ name: '角色甲' }} onSave={onSave} onClose={() => {}} />)

    fireEvent.click(document.querySelector('.edit-card-modal .btn-primary'))

    await waitFor(() => expect(document.querySelector('.edit-card-modal .error-box')).toBeInTheDocument())
    expect(document.querySelector('.edit-card-modal .error-box').textContent).toContain('保存失败，请稍后再试')
    expect(document.querySelector('.edit-card-modal')).toBeInTheDocument()
    expect(document.querySelector('.edit-card-modal .btn-primary').textContent).toBe('保存')
    expect(onSave).toHaveBeenCalledTimes(1)
  })
})
