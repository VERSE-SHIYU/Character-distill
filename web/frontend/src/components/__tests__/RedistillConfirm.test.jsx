import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import useAppStore from '../../store/useAppStore'
import RedistillConfirm from '../RedistillConfirm'
import { postJSON } from '../../api/client'

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => new Promise(() => {})), postJSON: vi.fn(), streamSSE: vi.fn(),
  getToken: vi.fn(() => null), setToken: vi.fn(), removeToken: vi.fn(), setRefreshToken: vi.fn(), removeAuth: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(postJSON).mockReset()
  useAppStore.setState({ texts: [{ id: 't1', title: '某书' }], redistillPrompt: null, distillTasks: [] })
})

describe('G6 重新蒸馏确认弹窗', () => {
  it.fails('写出角色名和书名；取消不发请求', async () => {
    let p
    await act(async () => { p = useAppStore.getState().distillCharacter('t1', '祈灼', true) })
    render(<RedistillConfirm />)
    expect(screen.getByText('重新蒸馏「祈灼」？')).toBeTruthy()
    expect(screen.getByText(/《某书》/)).toBeTruthy()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '取消' })) })
    expect(await p).toBe(false)
    expect(screen.queryByText('重新蒸馏「祈灼」？')).toBeNull()
    expect(postJSON).not.toHaveBeenCalled()
  })
})
