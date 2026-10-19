import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import DistillTaskBar from '../DistillTaskBar'
import DistillWorkbench from '../DistillWorkbench'

// F2 E7：任务栏与工作台任务详情的续跑入口，distillCharacter 第 4 个参数分别是
// task_bar / workbench_task。mock 整个 store（参照 CharCardHeader.test.jsx），
// 只断言调用参数 —— 请求体由 E6 守。

if (typeof window !== 'undefined') {
  window.matchMedia = (query) => ({
    matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  })
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
}

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => new Promise(() => {})),
  getAuthHeaders: vi.fn(() => ({})), exportCard: vi.fn(),
}))
vi.mock('../../api/cards', () => ({ fetchAllCards: vi.fn(() => Promise.resolve([])) }))

const { mockState } = vi.hoisted(() => ({ mockState: {} }))
vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

const TASK = {
  id: 'tk1', task_id: 'tk1', textId: 'x1', character: '祈灼',
  status: 'interrupted', done: true, actions: ['resume'], progress_pct: 30, message: '',
}
const base = () => ({
  authUser: { id: 'u1', role: 'user' }, currentTextId: 'x1',
  texts: [{ id: 'x1', title: '某书' }], distillTasks: [TASK],
  setView: vi.fn(), pushView: vi.fn(), loadCards: vi.fn(), loadTexts: vi.fn(),
  removeDistillTask: vi.fn(), cancelDistillTask: vi.fn(),
  distillCharacter: vi.fn(),
  error: null,
})

beforeEach(() => {
  window.innerWidth = 1024
  for (const k of Object.keys(mockState)) delete mockState[k]
  Object.assign(mockState, base())
})

describe('E7 续跑入口带上 entry', () => {
  it('任务栏「继续蒸馏」→ task_bar', async () => {
    render(<DistillTaskBar />)
    fireEvent.click(document.querySelector('.distill-fab'))
    fireEvent.click(document.querySelector('.distill-task-retry[title="继续蒸馏"]'))
    expect(mockState.distillCharacter).toHaveBeenCalledWith('x1', '祈灼', false, 'task_bar')
  })

  it('工作台任务详情「继续蒸馏」→ workbench_task', async () => {
    render(<DistillWorkbench />)
    const btn = () => [...document.querySelectorAll('.dw-cta button')].find((b) => b.textContent.includes('继续蒸馏'))
    await waitFor(() => expect(btn()).toBeTruthy())
    fireEvent.click(btn())
    expect(mockState.distillCharacter).toHaveBeenCalledWith('x1', '祈灼', false, 'workbench_task')
  })
})
