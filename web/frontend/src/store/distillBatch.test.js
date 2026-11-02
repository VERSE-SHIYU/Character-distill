import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { postJSON } from '../api/client'

// D25 第 3 步：批量蒸馏的请求体、任务登记、错误文案只走 store 一处。

vi.mock('../api/client', () => ({
  fetchWithTimeout: vi.fn(() => new Promise(() => {})), postJSON: vi.fn(), streamSSE: vi.fn(),
  getToken: vi.fn(() => null), setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(), removeAuth: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(postJSON).mockReset()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  localStorage.removeItem('distill_tasks')
  useAppStore.setState({ distillTasks: [], error: null })
})

describe('distillBatch', () => {
  it('T1 请求体正确，返回的任务全部登记为 queued', async () => {
    postJSON.mockResolvedValue({
      tasks: [{ name: '甲', task_id: 'k1' }, { name: '丙', task_id: 'k2' }],
      errors: [],
    })
    const started = await useAppStore.getState().distillBatch('t1', ['甲', '丙'])
    expect(postJSON).toHaveBeenCalledWith('/api/distill/batch',
      { text_id: 't1', names: ['甲', '丙'], entry: 'character_page' })
    const tasks = useAppStore.getState().distillTasks
    expect(tasks).toHaveLength(2)
    expect(tasks.map((t) => t.status)).toEqual(['queued', 'queued'])
    expect(started).toBe(2)
  })

  it('T2 部分失败：错误文案进 error，成功的仍登记', async () => {
    postJSON.mockResolvedValue({
      tasks: [{ name: '甲', task_id: 'k1' }],
      errors: [{ name: '丙', detail: 'x' }],
    })
    await useAppStore.getState().distillBatch('t1', ['甲', '丙'])
    expect(useAppStore.getState().error).toContain('丙：x')
    expect(useAppStore.getState().distillTasks).toHaveLength(1)
  })

  it('T3 整批 429：后端文案原样进 error，没有新任务', async () => {
    const msg = '你已有蒸馏任务在运行（每用户同时最多 3 个），请等它完成或停止后再试'
    postJSON.mockRejectedValue(new Error(msg))
    await useAppStore.getState().distillBatch('t1', ['甲', '丙'])
    expect(useAppStore.getState().error).toBe(msg)
    expect(useAppStore.getState().distillTasks).toHaveLength(0)
  })

  it('T4 addDistillTask 不传 status 时占位为 running（对照）', () => {
    useAppStore.getState().addDistillTask('k', 't1', '甲')
    expect(useAppStore.getState().distillTasks[0].status).toBe('running')
  })

  it('T5 taskLabel 对内存细化状态取表里的字', async () => {
    const { taskLabel } = await import('../components/distillStatus')
    expect(taskLabel({ status: 'analyzing', progress_pct: 0 })).toBe('生成人设')
  })
})
