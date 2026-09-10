import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore, { normalizeTask, isTerminal, taskActions } from './useAppStore'
import { fetchWithTimeout } from '../api/client'

vi.mock('../api/client', () => ({
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  fetchWithTimeout: vi.fn(),
  getToken: vi.fn(),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
}))

const task = (over = {}) => ({ task_id: 't1', status: 'running', ...over })

beforeEach(() => {
  vi.resetAllMocks()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

describe('normalizeTask 入参门', () => {
  it('缺 task_id → null', () => {
    expect(normalizeTask({ status: 'running' })).toBeNull()
  })

  it('缺 status → null', () => {
    expect(normalizeTask({ task_id: 't1' })).toBeNull()
  })

  it('SSE done_payload（有 done 无 status）被挡', () => {
    expect(normalizeTask({ done: true, awakening: 'x' })).toBeNull()
  })

  it('非对象 → null', () => {
    expect(normalizeTask(null)).toBeNull()
    expect(normalizeTask('t1')).toBeNull()
  })
})

describe('normalizeTask poll_after_ms 钳位', () => {
  it.each([
    [0, 500],
    [-1, 500],
    [999999, 30000],
    [undefined, 3000],
    [null, 3000],
    ['abc', 3000],
    [1500, 1500],
  ])('poll_after_ms=%s → %s', (input, expected) => {
    expect(normalizeTask(task({ poll_after_ms: input })).poll_after_ms).toBe(expected)
  })
})

describe('normalizeTask done 兜底（rollout shim）', () => {
  it('服务端 done 优先于兜底集合', () => {
    expect(normalizeTask(task({ status: 'running', done: true })).done).toBe(true)
    expect(normalizeTask(task({ status: 'done', done: false })).done).toBe(false)
  })

  it('done 缺失时按兜底集合判终态', () => {
    for (const s of ['done', 'error', 'interrupted']) {
      expect(normalizeTask(task({ status: s })).done).toBe(true)
    }
    expect(normalizeTask(task({ status: 'running' })).done).toBe(false)
  })
})

describe('normalizeTask actions 归一', () => {
  it('非数组 → []', () => {
    expect(normalizeTask(task({ actions: null })).actions).toEqual([])
    expect(normalizeTask(task({ actions: 'cancel' })).actions).toEqual([])
  })

  it('未知 token 保留且不抛', () => {
    expect(normalizeTask(task({ actions: ['cancel', 'teleport'] })).actions)
      .toEqual(['cancel', 'teleport'])
  })
})

describe('isTerminal / taskActions', () => {
  it('四态 + 未知态', () => {
    expect(isTerminal(normalizeTask(task({ status: 'running' })))).toBe(false)
    expect(isTerminal(normalizeTask(task({ status: 'done' })))).toBe(true)
    expect(isTerminal(normalizeTask(task({ status: 'error' })))).toBe(true)
    expect(isTerminal(normalizeTask(task({ status: 'interrupted' })))).toBe(true)
    expect(isTerminal(normalizeTask(task({ status: 'mystery' })))).toBe(false)
  })

  it('taskActions 恒为数组', () => {
    expect(taskActions(undefined)).toEqual([])
    expect(taskActions({ actions: 'x' })).toEqual([])
  })
})

describe('旧形状 localStorage 兼容', () => {
  it('补 task_id 后可归一化并保留 id', () => {
    const legacy = { id: 'abc', textId: 'x', character: 'A', status: 'running' }
    const t = normalizeTask({ ...legacy, task_id: legacy.task_id ?? legacy.id })
    expect(t).not.toBeNull()
    expect(t.id).toBe('abc')
    expect(isTerminal(t)).toBe(false)
  })
})

describe('轮询在终态停止', () => {
  it('done 响应后不再排下一次请求', async () => {
    vi.useFakeTimers()
    useAppStore.setState({ distillTasks: [], distilling: false })
    fetchWithTimeout.mockResolvedValue({
      status: 200,
      json: async () => ({ task_id: 't1', status: 'done', done: true, actions: [], poll_after_ms: 0, progress_pct: 100, character: 'A' }),
    })

    useAppStore.getState().addDistillTask('t1', 'x', 'A')
    expect(fetchWithTimeout).toHaveBeenCalledTimes(0) // 首次是 setTimeout，尚未触发

    await vi.advanceTimersByTimeAsync(3000) // 第一拍
    const afterTerminal = fetchWithTimeout.mock.calls.length
    expect(afterTerminal).toBeGreaterThan(0)

    await vi.advanceTimersByTimeAsync(120000) // 终态后无论如何不再打
    expect(fetchWithTimeout.mock.calls.length).toBe(afterTerminal)
    vi.useRealTimers()
  })

  it('interrupted 响应后不再排下一次请求', async () => {
    vi.useFakeTimers()
    useAppStore.setState({ distillTasks: [], distilling: false })
    fetchWithTimeout.mockResolvedValue({
      status: 200,
      json: async () => ({ task_id: 't2', status: 'interrupted', done: true, actions: ['resume'], poll_after_ms: 0, progress_pct: 30, character: 'A' }),
    })

    useAppStore.getState().addDistillTask('t2', 'x', 'A')
    await vi.advanceTimersByTimeAsync(3000)
    const afterTerminal = fetchWithTimeout.mock.calls.length

    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchWithTimeout.mock.calls.length).toBe(afterTerminal)
    // 终态仍留在列表里（可续跑），不是被移除
    expect(useAppStore.getState().distillTasks.some((t) => t.id === 't2')).toBe(true)
    vi.useRealTimers()
  })

  it('404 → 从列表移除并停止轮询（不再标 error 留着）', async () => {
    vi.useFakeTimers()
    useAppStore.setState({ distillTasks: [], distilling: false })
    fetchWithTimeout.mockRejectedValue(Object.assign(new Error('gone'), { status: 404 }))

    useAppStore.getState().addDistillTask('t3', 'x', 'A')
    await vi.advanceTimersByTimeAsync(3000)
    const after404 = fetchWithTimeout.mock.calls.length

    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchWithTimeout.mock.calls.length).toBe(after404)
    expect(useAppStore.getState().distillTasks.some((t) => t.id === 't3')).toBe(false)
    vi.useRealTimers()
  })
})

describe('重启恢复（kill -9 后重开页面）', () => {
  it('DB 为 interrupted：停止轮询、保留「继续蒸馏」动作', async () => {
    vi.useFakeTimers()
    localStorage.setItem('distill_tasks', JSON.stringify([
      { id: 't9', task_id: 't9', textId: 'x', character: 'A', status: 'running' },
    ]))
    useAppStore.setState({ distillTasks: [], distilling: false })
    fetchWithTimeout.mockResolvedValue({
      status: 200,
      json: async () => ({ task_id: 't9', status: 'interrupted', done: true, actions: ['resume'], poll_after_ms: 0, progress_pct: 40, character: 'A' }),
    })

    useAppStore.getState().restoreDistillTasks()
    await vi.advanceTimersByTimeAsync(0) // 放行恢复时的首次查询
    const afterRestore = fetchWithTimeout.mock.calls.length
    expect(afterRestore).toBeGreaterThan(0)

    await vi.advanceTimersByTimeAsync(120000) // 关键：没有起轮询
    expect(fetchWithTimeout.mock.calls.length).toBe(afterRestore)

    const t = useAppStore.getState().distillTasks.find((x) => x.id === 't9')
    expect(t.status).toBe('interrupted')
    expect(taskActions(t)).toEqual(['resume'])
    expect(isTerminal(t)).toBe(true)
    localStorage.removeItem('distill_tasks')
    vi.useRealTimers()
  })
})
