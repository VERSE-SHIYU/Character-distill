import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import DistillTaskBar from '../DistillTaskBar'
import DistillWorkbench from '../DistillWorkbench'
import useAppStore from '../../store/useAppStore'
import { fetchWithTimeout, postJSON } from '../../api/client'

// G8 入口 × 是否弹窗：弹不弹由调用方传的 force 决定（store 只认 force），
// 所以每个入口「传对 force」是目标的另一半。走真实 store + mock 网络层，断言用户可见的结果：
// 弹窗待确认（redistillPrompt）还是直接发 /api/distill/start。
// 角色页两个入口在 RedistillCharCard.test.jsx（那里 mock 整个 store）。

if (typeof window !== 'undefined') {
  window.matchMedia = (query) => ({
    matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  })
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(), getAuthHeaders: vi.fn(() => ({})), postJSON: vi.fn(), streamSSE: vi.fn(),
  getToken: vi.fn(() => null), setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(),
  removeAuth: vi.fn(), exportCard: vi.fn(),
}))

const TEXT = { id: 'x1', title: '某书' }
const CARD = { id: 'c1', name: '祈灼', text_id: 'x1', card_json: '{"name":"祈灼"}' }
const task = (actions) => ({ id: 'tk1', textId: 'x1', character: '祈灼', actions, done: true, progress_pct: 30 })
const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })

beforeEach(() => {
  window.innerWidth = 1024
  vi.mocked(postJSON).mockReset()
  vi.mocked(postJSON).mockResolvedValue({ task_id: 'tk2' })
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockImplementation((url) => {
    if (url === '/api/distill/cards/by-text/x1') return ok([CARD])
    if (url === '/api/distill/cards/standalone') return ok([])
    return new Promise(() => {}) // 轮询等其余请求挂起，不影响断言
  })
  useAppStore.setState({ authUser: null, texts: [TEXT], distillTasks: [], distilling: false, redistillPrompt: null, error: null })
})

const expectStartedWithoutPrompt = async () => {
  await waitFor(() => expect(postJSON).toHaveBeenCalledTimes(1))
  expect(postJSON).toHaveBeenCalledWith('/api/distill/start', { text_id: 'x1', character_name: '祈灼', force: false })
  expect(useAppStore.getState().redistillPrompt).toBeNull()
}
const expectPromptWithoutStart = async () => {
  await waitFor(() => expect(useAppStore.getState().redistillPrompt).toMatchObject({ textId: 'x1', name: '祈灼' }))
  expect(postJSON).not.toHaveBeenCalled()
}

const clickTaskBar = (title) => {
  fireEvent.click(document.querySelector('.distill-fab'))
  fireEvent.click(document.querySelector(`.distill-task-retry[title="${title}"]`))
}
const clickWorkbenchCta = async (label) => {
  const find = () => [...document.querySelectorAll('.dw-cta button')].find((b) => b.textContent.includes(label))
  await waitFor(() => expect(find()).toBeTruthy())
  fireEvent.click(find())
}

describe('G8 入口 × 是否弹窗', () => {
  it.each([
    ['任务栏 出错「重新蒸馏」', ['retry'], () => { render(<DistillTaskBar />); clickTaskBar('重新蒸馏') }],
    ['任务栏 中断「继续蒸馏」', ['resume'], () => { render(<DistillTaskBar />); clickTaskBar('继续蒸馏') }],
    ['工作台任务 出错「重新蒸馏」', ['retry'], async () => { render(<DistillWorkbench />); await clickWorkbenchCta('重新蒸馏') }],
    ['工作台任务 中断「继续蒸馏」', ['resume'], async () => { render(<DistillWorkbench />); await clickWorkbenchCta('继续蒸馏') }],
  ])('%s：续跑，不弹窗，force=false', async (_name, actions, act) => {
    useAppStore.setState({ distillTasks: [task(actions)] })
    await act()
    await expectStartedWithoutPrompt()
  })

  it('工作台卡片详情「重新蒸馏」：弹窗，先不发请求', async () => {
    render(<DistillWorkbench />)
    await waitFor(() => expect(document.querySelector('.dw-detail-scroll')).toBeInTheDocument())
    const btn = () => [...document.querySelectorAll('.dw-detail-scroll button')].find((b) => b.textContent.includes('重新蒸馏'))
    await waitFor(() => expect(btn()).toBeTruthy())
    fireEvent.click(btn())
    await expectPromptWithoutStart()
  })
})
