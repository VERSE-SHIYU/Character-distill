import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore, { DISTILL_ENTRY } from './useAppStore'
import { postJSON } from '../api/client'

// F2 目标检查（前端一侧，spec docs/specs/distill-entry-audit.md §一 E6）：
// distillCharacter 把 entry 原样带进 /api/distill/start 的请求体；入口取值只来自 DISTILL_ENTRY 一处。

vi.mock('../api/client', () => ({
  fetchWithTimeout: vi.fn(() => new Promise(() => {})), postJSON: vi.fn(), streamSSE: vi.fn(),
  getToken: vi.fn(() => null), setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(), removeAuth: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(postJSON).mockReset()
  vi.mocked(postJSON).mockResolvedValue({ task_id: 'tk1' })
  useAppStore.setState({ distillTasks: [], error: null })
})

describe('E6 入口随请求发出', () => {
  it('取值表与后端约定一致', () => {
    expect(DISTILL_ENTRY).toEqual({
      characterPage: 'character_page', workbenchCard: 'workbench_card',
      workbenchTask: 'workbench_task', taskBar: 'task_bar',
    })
  })
  it('请求体带 entry', async () => {
    await useAppStore.getState().distillCharacter('t1', '甲', false, DISTILL_ENTRY.taskBar)
    expect(postJSON).toHaveBeenCalledWith('/api/distill/start',
      { text_id: 't1', character_name: '甲', force: false, entry: 'task_bar' })
  })
})
