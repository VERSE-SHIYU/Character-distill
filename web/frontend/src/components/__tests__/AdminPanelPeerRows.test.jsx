import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminPanel from '../AdminPanel'
import { adminAPI } from '../../api/client'

// 联邦用户列表里对端行只留「禁用/启用」一个写入口，且它走跨节点接口：本页其余写接口
// 都只按本库 user_id 打，而 offerPass 在两端整号复制、id 相同 —— 点对端那行的删除 /
// 重置密码，打到的是**本节点**同 id 的账号。行的身份是 (节点, id)，不是裸 id。

const LOCAL_X = { id: 'x1', username: 'local-x', role: 'user', node_region: 'local', is_disabled: false }
// 真实白名单（隐私政策第 (5) 条）不含 role —— 对端下发的那份没有这个字段。
const PEER_X = { id: 'x1', username: 'peer-x', node_region: 'peer', is_disabled: false }
// 勾选那条用**不同 id**：两端同 id 时「选中的 id 集合」大小恒为 1，分辨不出对端被选进来。
const PEER_P = { id: 'p2', username: 'peer-p', node_region: 'peer', is_disabled: false }

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
    listUsersFederated: vi.fn(() => Promise.resolve({ local: [LOCAL_X], peer: [PEER_X] })),
    setUserRole: vi.fn(() => Promise.resolve({})),
    disableUser: vi.fn(() => Promise.resolve({})),
    enableUser: vi.fn(() => Promise.resolve({})),
    peerDisableUser: vi.fn(() => Promise.resolve({})),
    peerEnableUser: vi.fn(() => Promise.resolve({})),
  },
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
}))

async function openUsersTab() {
  const utils = render(<AdminPanel />)
  fireEvent.click(screen.getByRole('button', { name: /用户管理/ }))
  // 本地行（同 id）有角色下拉；对端行没有 —— 等它出现即代表两行都渲染完了。
  await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
  return utils
}

beforeEach(() => {
  for (const fn of ['disableUser', 'enableUser', 'peerDisableUser', 'peerEnableUser']) {
    adminAPI[fn].mockReset()
    adminAPI[fn].mockResolvedValue({})
  }
  adminAPI.setUserRole.mockReset()
  adminAPI.setUserRole.mockResolvedValue({})
  adminAPI.listUsersFederated.mockResolvedValue({ local: [LOCAL_X], peer: [PEER_X] })
})

describe('AdminPanel 对端行', () => {
  it('对端行只有「禁用」一个写按钮 + 提示，同 id 的本地行 5 个照常', async () => {
    const { container } = await openUsersTab()
    const rows = container.querySelectorAll('.admin-table tbody tr')
    expect(rows).toHaveLength(2)

    const localActions = rows[0].querySelector('.admin-actions-cell')
    const peerActions = rows[1].querySelector('.admin-actions-cell')
    const peerButtons = [...peerActions.querySelectorAll('button')].map((b) => b.textContent)
    expect(peerButtons, '对端行只该剩禁用一个写按钮').toEqual(['禁用'])
    expect(peerActions.textContent).toContain('其余操作请在对端节点进行')
    expect(localActions.querySelectorAll('button'), '同 id 的本地行按钮不该被连带去掉').toHaveLength(5)
  })

  it('对端行禁用走跨节点接口，不碰本节点同 id 的账号', async () => {
    const { container } = await openUsersTab()
    const peerRow = container.querySelectorAll('.admin-table tbody tr')[1]
    fireEvent.click(peerRow.querySelector('.admin-actions-cell button'))
    expect(screen.getByText(/确定禁用对端节点的用户/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确定' }))
    await waitFor(() => expect(adminAPI.peerDisableUser).toHaveBeenCalledWith('x1'))
    expect(adminAPI.disableUser, '打到了本节点同 id 的账号').not.toHaveBeenCalled()
  })

  it('已禁用的对端行启用走跨节点接口', async () => {
    adminAPI.listUsersFederated.mockResolvedValue({ local: [LOCAL_X], peer: [{ ...PEER_X, is_disabled: true }] })
    const { container } = await openUsersTab()
    const peerRow = container.querySelectorAll('.admin-table tbody tr')[1]
    fireEvent.click(peerRow.querySelector('.admin-actions-cell button'))
    await waitFor(() => expect(adminAPI.peerEnableUser).toHaveBeenCalledWith('x1'))
    expect(adminAPI.enableUser).not.toHaveBeenCalled()
  })

  it('自己那两行（本地 + 对端同 id）都不给禁用按钮', async () => {
    const SELF_L = { ...LOCAL_X, id: 'admin1', username: 'me', role: 'admin' }
    const SELF_P = { ...PEER_X, id: 'admin1', username: 'me-peer' }
    adminAPI.listUsersFederated.mockResolvedValue({ local: [SELF_L, LOCAL_X], peer: [SELF_P] })
    const { container } = await openUsersTab()
    const rows = [...container.querySelectorAll('.admin-table tbody tr')]
    expect(rows).toHaveLength(3)
    // 行序 = 本地在前、对端在后：rows[0] 本地自己、rows[2] 对端自己
    for (const r of [rows[0], rows[2]]) {
      const labels = [...r.querySelectorAll('.admin-actions-cell button')].map((b) => b.textContent)
      expect(labels, '自己那行出现了禁用按钮').not.toContain('禁用')
    }
  })

  it('同 id 的本地行与对端行各是一行，不报重复 key', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = await openUsersTab()
    expect(container.querySelectorAll('.admin-table tbody tr')).toHaveLength(2)
    const dupKey = errSpy.mock.calls.some((c) => String(c[0]).includes('same key'))
    expect(dupKey, '同 id 两行共用了一个 React key').toBe(false)
    errSpy.mockRestore()
  })

  it('对端行没有勾选框；全选只选本节点可删的那 1 人', async () => {
    adminAPI.listUsersFederated.mockResolvedValue({ local: [LOCAL_X], peer: [PEER_P] })
    const { container } = await openUsersTab()
    const rowBoxes = container.querySelectorAll('.admin-table tbody input[type="checkbox"]')
    expect(rowBoxes, '对端行渲染了勾选框').toHaveLength(1)

    fireEvent.click(container.querySelector('.admin-table thead input[type="checkbox"]'))
    await waitFor(() => expect(screen.getByText('已选 1 个用户')).toBeInTheDocument())
  })

  it('对端行角色显示「—」，不给下拉', async () => {
    const { container } = await openUsersTab()
    const rows = container.querySelectorAll('.admin-table tbody tr')
    expect(rows[1].querySelector('[role="combobox"]')).toBeNull()
    expect(rows[1].textContent).toContain('—')
  })
})
