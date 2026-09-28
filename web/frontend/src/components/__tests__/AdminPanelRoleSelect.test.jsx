import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminPanel from '../AdminPanel'
import { adminAPI } from '../../api/client'

// 角色下拉换成 Select 后的接线：改的是「选中的值有没有原样交给 changeRole」，
// 以及 rolePending 期间那一行的下拉确实禁用。AdminPanel 内部不是请求路径，
// 所以只 mock adminAPI 的边界。

const ROW = { id: 'u2', username: 'bob', role: 'user', node_region: 'local', is_disabled: false }

const { mockState } = vi.hoisted(() => ({
  mockState: { authUser: { id: 'admin1', role: 'admin' }, popView: () => {} },
}))

vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

vi.mock('../../api/client', () => ({
  adminAPI: {
    getDashboard: vi.fn(() => Promise.resolve({})),
    listUsersFederated: vi.fn(() => Promise.resolve({ local: [ROW], peer: [] })),
    setUserRole: vi.fn(() => Promise.resolve({})),
  },
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
}))

async function openUsersTab() {
  render(<AdminPanel />)
  fireEvent.click(screen.getByRole('button', { name: /用户管理/ }))
  await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
}

beforeEach(() => {
  adminAPI.setUserRole.mockReset()
  adminAPI.setUserRole.mockResolvedValue({})
  adminAPI.listUsersFederated.mockResolvedValue({ local: [ROW], peer: [] })
})

describe('AdminPanel 角色下拉接线', () => {
  it('选「管理员」后按 (用户 id, admin) 提交', async () => {
    await openUsersTab()
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('option', { name: '管理员' }))
    await waitFor(() => expect(adminAPI.setUserRole).toHaveBeenCalledWith('u2', 'admin'))
  })

  it('rolePending 期间该行下拉禁用', async () => {
    adminAPI.setUserRole.mockReturnValue(new Promise(() => {})) // 请求不返回
    await openUsersTab()
    fireEvent.click(screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('option', { name: '管理员' }))
    await waitFor(() => expect(screen.getByRole('combobox')).toBeDisabled())
  })
})
