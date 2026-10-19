import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { postJSON } from '../api/client'

vi.mock('../api/client', () => ({
  fetchWithTimeout: vi.fn(() => new Promise(() => {})),
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(), removeAuth: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(postJSON).mockReset()
  vi.mocked(postJSON).mockResolvedValue({ task_id: 'tk1' })
  useAppStore.setState({ distillTasks: [], redistillPrompt: null, error: null })
})
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('重新蒸馏确认', () => {
  it.fails('G1 force=true 先不发请求', async () => {
    useAppStore.getState().distillCharacter('t1', '祈灼', true)
    await flush()
    expect(postJSON).not.toHaveBeenCalled()
    expect(useAppStore.getState().redistillPrompt).toMatchObject({ textId: 't1', name: '祈灼' })
  })
  it.fails('G2 确认后发一次 force=true，返回 true', async () => {
    const p = useAppStore.getState().distillCharacter('t1', '祈灼', true)
    await flush()
    useAppStore.getState().confirmRedistill()
    expect(await p).toBe(true)
    expect(postJSON).toHaveBeenCalledTimes(1)
    expect(postJSON).toHaveBeenCalledWith('/api/distill/start', { text_id: 't1', character_name: '祈灼', force: true })
    expect(useAppStore.getState().redistillPrompt).toBeNull()
  })
  it.fails('G3 取消不发请求，返回 false', async () => {
    const p = useAppStore.getState().distillCharacter('t1', '祈灼', true)
    await flush()
    useAppStore.getState().cancelRedistill()
    expect(await p).toBe(false)
    expect(postJSON).not.toHaveBeenCalled()
    expect(useAppStore.getState().redistillPrompt).toBeNull()
  })
  it.fails('G4 force=false 立即发', async () => {
    const p = useAppStore.getState().distillCharacter('t1', '祈灼', false)
    expect(await p).toBe(true)
    expect(postJSON).toHaveBeenCalledWith('/api/distill/start', { text_id: 't1', character_name: '祈灼', force: false })
    expect(useAppStore.getState().redistillPrompt).toBeNull()
  })
  it.fails('G5 启动失败返回 false', async () => {
    vi.mocked(postJSON).mockRejectedValueOnce(new Error('boom'))
    expect(await useAppStore.getState().distillCharacter('t1', '祈灼', false)).toBe(false)
  })
})
