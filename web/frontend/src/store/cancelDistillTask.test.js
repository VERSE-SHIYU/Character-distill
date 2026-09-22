import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { fetchWithTimeout } from '../api/client'

// 106：取消蒸馏原来是 `DELETE.catch(() => {})` 后无条件把任务移出列表 ——
// 服务端拒绝时界面显示「已取消」，任务其实还在跑。现在 DELETE 成功才移出，
// 失败保留任务并把 detail 抛给调用方。

vi.mock('../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
}))

const task = (over = {}) => ({ id: 't1', character: '角色甲', actions: ['cancel'], done: false, ...over })

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  useAppStore.setState({ distillTasks: [task()], distilling: true })
})

describe('cancelDistillTask', () => {
  it('DELETE 成功：任务移出列表', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValueOnce({ ok: true, status: 200 })

    await useAppStore.getState().cancelDistillTask(task())

    expect(fetchWithTimeout).toHaveBeenCalledWith('/api/distill/task/t1', { method: 'DELETE' })
    expect(useAppStore.getState().distillTasks).toHaveLength(0)
  })

  it('DELETE 失败：任务留在列表里，错误抛给调用方', async () => {
    vi.mocked(fetchWithTimeout).mockRejectedValueOnce(
      Object.assign(new Error('无权取消该任务'), { status: 403 }))

    await expect(useAppStore.getState().cancelDistillTask(task())).rejects.toThrow('无权取消该任务')
    expect(useAppStore.getState().distillTasks).toHaveLength(1)
  })

  it('没有 cancel 动作：纯本地移除，一个请求都不发', async () => {
    await useAppStore.getState().cancelDistillTask(task({ actions: ['resume'] }))

    expect(fetchWithTimeout).not.toHaveBeenCalled()
    expect(useAppStore.getState().distillTasks).toHaveLength(0)
  })
})
