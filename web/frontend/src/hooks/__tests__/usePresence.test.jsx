import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { SWRConfig } from 'swr'
import usePresence from '../usePresence'
import { fetchWithTimeout } from '../../api/client'

vi.mock('../../api/client', () => ({ fetchWithTimeout: vi.fn() }))
const reply = (body) => ({ json: () => Promise.resolve(body) })
const urls = () => vi.mocked(fetchWithTimeout).mock.calls.map(([u]) => u)
// 每个用例独立缓存，不串数据
const wrapper = ({ children }) => <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
const hook = (initialProps, opts) => renderHook(({ id }) => usePresence(id, opts), { initialProps, wrapper })

beforeEach(() => vi.mocked(fetchWithTimeout).mockReset())

describe('usePresence（SWR 封装）', () => {
  it('P1 按传入的 userId 查询，并映射字段', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValue(reply({ online: false, hidden: false, last_active_at: '2026-09-28T08:00:00Z' }))
    const { result } = hook({ id: 'u2' })
    await waitFor(() => expect(result.current.online).toBe(false))
    expect(urls()).toEqual(['/api/auth/user/u2/online'])
    expect(result.current.lastActive).toBe('2026-09-28T08:00:00Z')
  })

  it('P2 对方隐藏：online 为 null、hidden 为 true', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValue(reply({ online: null, last_active_at: null, hidden: true }))
    const { result } = hook({ id: 'u2' })
    await waitFor(() => expect(result.current.hidden).toBe(true))
    expect(result.current.online).toBe(null)
  })

  it('P3 换人后旧响应晚到也不覆盖新的人', async () => {
    let releaseOld
    vi.mocked(fetchWithTimeout)
      .mockImplementationOnce(() => new Promise((r) => { releaseOld = () => r(reply({ online: true, hidden: false })) }))
      .mockResolvedValueOnce(reply({ online: false, hidden: false, last_active_at: '' }))
    const { result, rerender } = hook({ id: 'u2' })
    rerender({ id: 'u3' })
    await waitFor(() => expect(result.current.online).toBe(false))
    await act(async () => { releaseOld() })
    expect(result.current.online).toBe(false)
  })

  it('P4 没有 userId 时不发请求', () => {
    hook({ id: null })
    expect(fetchWithTimeout).not.toHaveBeenCalled()
  })

  it('P5 已显示上一个人的状态后换人，立刻清空', async () => {
    let releaseNew
    vi.mocked(fetchWithTimeout)
      .mockResolvedValueOnce(reply({ online: true, hidden: false, last_active_at: '' }))
      .mockImplementationOnce(() => new Promise((r) => { releaseNew = () => r(reply({ online: false, hidden: false, last_active_at: '' })) }))
    const { result, rerender } = hook({ id: 'u2' })
    await waitFor(() => expect(result.current.online).toBe(true))
    rerender({ id: 'u3' })
    expect(result.current.online).toBe(null)
    await act(async () => { releaseNew() })
    expect(result.current.online).toBe(false)
  })

  it('P6 refreshInterval 生效：定时重新查询', async () => {
    vi.mocked(fetchWithTimeout).mockResolvedValue(reply({ online: true, hidden: false, last_active_at: '' }))
    hook({ id: 'u2' }, { refreshInterval: 40 })
    await waitFor(() => expect(urls().length).toBeGreaterThanOrEqual(3), { timeout: 1000 })
  })
})
