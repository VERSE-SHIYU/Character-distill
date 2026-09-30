import { describe, it, expect, vi } from 'vitest'
import useAppStore from './useAppStore'

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

// 新打开的页面默认走 Agent 工具编排（场景 / 记忆 / 联网）。后端请求模型的
// 默认值由 tests/test_agent_mode_default.py 钉住，两边必须一致。
describe('agentMode 默认值', () => {
  it('store 初始状态为开启', () => {
    expect(useAppStore.getInitialState().agentMode).toBe(true)
  })
})
