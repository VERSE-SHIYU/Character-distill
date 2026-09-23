import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import DistillTaskBar from '../DistillTaskBar'
import DistillWorkbench from '../DistillWorkbench'
import TextPanel from '../TextPanel'
import useAppStore from '../../store/useAppStore'
import { fetchWithTimeout } from '../../api/client'
import { fetchAllCards } from '../../api/cards'

// 106 的两个落点是逐字重复的两份代码（任务条 / 工作台）。两份都要被守 ——
// 只测一份，另一份以后再次把 DELETE 吞掉也不会红。
// 走真实 store + mock 网络层，这样顺带守住「失败时 removeDistillTask 没被调用」。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false, media: query,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null,
      dispatchEvent: () => false,
    })
  }
  window.matchMedia = (query) => {
    const m = /max-width:\s*(\d+)px/.exec(query)
    return {
      matches: m ? window.innerWidth <= Number(m[1]) : false,
      media: query,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null,
      dispatchEvent: () => false,
    }
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
  exportCard: vi.fn(),
}))

const TASK = { id: 't1', character: '角色甲', actions: ['cancel'], done: false, progress_pct: 40 }

// 组件挂载时会自己去拉卡片列表，所以不能按调用次数排 mock —— 按 method 分流。
const mockDelete = (behavior) => {
  vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
    if (opts?.method === 'DELETE') return behavior()
    return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve([]) })
  })
}
// fetchWithTimeout 失败时抛的就是带 detail 的 AppError，这里最小化复现
const deleteFails = () => mockDelete(() => Promise.reject(
  Object.assign(new Error('请先登录后再操作'), { status: 403 })))
const deleteOk = () => mockDelete(() => Promise.resolve({ ok: true, status: 200 }))

const clickCancel = () => {
  const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('暂停任务'))
  fireEvent.click(btn)
}
const drawerCancel = () => {
  const fab = document.querySelector('.distill-fab')
  fireEvent.click(fab)
  fireEvent.click(document.querySelector('.distill-task-close[title="取消蒸馏"]'))
}

beforeEach(() => {
  window.innerWidth = 1024
  vi.mocked(fetchWithTimeout).mockReset()
  // 116 之后 texts 可以给空数组了：拉卡片的 effect 只依赖 texts，
  // 文本列表由挂载时的 effect 拉一次，loadTexts 的「新数组」不再触发链条。
  useAppStore.setState({
    authUser: null, distillTasks: [TASK], distilling: true,
    texts: [],
  })
})

const textListCalls = () =>
  vi.mocked(fetchWithTimeout).mock.calls.filter(([url]) => url === '/api/text/list').length

// 旧代码在空列表下会 loadTexts → set 新数组 → effect 再跑，无限循环。
// 给它足够的时间跑几轮，再数请求次数。
const settleMs = (ms) => new Promise((r) => setTimeout(r, ms))

describe('106：取消失败不能假装成功', () => {
  describe('蒸馏任务条', () => {
    it('DELETE 报错：任务还在列表里，detail 显示出来', async () => {
      deleteFails()
      render(<DistillTaskBar />)

      drawerCancel()

      await waitFor(() => expect(document.querySelector('.error-box')).toBeInTheDocument())
      expect(document.querySelector('.error-box').textContent).toContain('请先登录后再操作')
      expect(document.querySelector('.distill-task-item')).toBeInTheDocument()
    })

    it('DELETE 成功：任务从列表消失', async () => {
      deleteOk()
      render(<DistillTaskBar />)

      drawerCancel()

      await waitFor(() => expect(document.querySelector('.distill-task-item')).toBeNull())
    })
  })

  describe('蒸馏工作台', () => {
    it('DELETE 报错：任务还在列表里，detail 显示出来', async () => {
      deleteFails()
      render(<DistillWorkbench />)

      await waitFor(() => expect(document.querySelector('.dw-cta')).toBeInTheDocument())
      clickCancel()

      await waitFor(() => expect(document.querySelector('.error-box')).toBeInTheDocument())
      expect(document.querySelector('.error-box').textContent).toContain('请先登录后再操作')
      expect(useAppStore.getState().distillTasks).toHaveLength(1)
    })

    it('DELETE 成功：任务从列表消失', async () => {
      deleteOk()
      render(<DistillWorkbench />)

      await waitFor(() => expect(document.querySelector('.dw-cta')).toBeInTheDocument())
      clickCancel()

      await waitFor(() => expect(useAppStore.getState().distillTasks).toHaveLength(0))
    })
  })
})

describe('116：文本列表为空时不再自触发', () => {
  it('蒸馏工作台：/api/text/list 只拉一次', async () => {
    deleteOk()
    render(<DistillWorkbench />)
    await waitFor(() => expect(document.querySelector('.dw-cta')).toBeInTheDocument())

    await settleMs(80)
    expect(textListCalls()).toBe(1)
  })

  it('创作页角色管理：/api/text/list 只拉一次', async () => {
    deleteOk()
    const { container } = render(<TextPanel />)
    fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
    await waitFor(() => expect(container.querySelector('.creation-tab')).toBeInTheDocument())

    await settleMs(80)
    expect(textListCalls()).toBe(1)
  })

  it('116 连带：没有文本时，独立卡片照样返回（不被空列表早返回吞掉）', async () => {
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      if (url === '/api/distill/cards/standalone') {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([{ id: 's1', name: '市场卡' }]) })
      }
      return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve([]) })
    })

    const cards = await fetchAllCards([])
    expect(cards).toHaveLength(1)
    expect(cards[0].id).toBe('s1')
    expect(cards[0]._source).toBe('来自市场')
  })
})
