/**
 * U11 / U12：开聊时选阶段的前端出口。
 *
 * U11 `startSessionBody(card)`：**可按**的卡带 `arc_phase`（默认最后阶段），不可按 / 无阶段为 `null`。
 * U12 身份赋值单一出口：恢复存档用 session 的值；新建用卡片默认值。
 * 均配 `vi.mock` 掉网络层 —— 这些出口是纯 state 计算，不打真接口。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import useAppStore from './useAppStore'
import { postJSON } from '../api/client'

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

const card3 = {
  id: 'c1', text_id: 't1',
  character_arc: { phases: [{}, {}, {}], selectable: true },
}

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  postJSON.mockResolvedValue({})
  useAppStore.setState({
    sessionId: null, currentCard: null, sessionUserRole: '', sessionArcPhase: null,
    arcPhasesByCard: {}, userRolesByCard: {}, messages: [], sending: false,
    pendingCard: null, currentView: 'chat',
  })
})

describe('U11 startSessionBody', () => {
  it('有阶段、未选过 → 默认最后阶段', () => {
    const body = useAppStore.getState().startSessionBody(card3)
    expect(body).toMatchObject({ text_id: 't1', card_id: 'c1', user_role: '' })
    expect(body.arc_phase).toBe(3)
  })

  it('选过阶段 1 → 请求体带 1', () => {
    useAppStore.getState().setArcPhase('c1', 1)
    expect(useAppStore.getState().startSessionBody(card3).arc_phase).toBe(1)
  })

  it('无阶段卡 → arc_phase 为 null', () => {
    const body = useAppStore.getState().startSessionBody({ id: 'c2', text_id: 't2' })
    expect(body).toHaveProperty('arc_phase')
    expect(body.arc_phase).toBeNull()
  })

  it('有阶段但不可按（起点不全 / 无指纹）→ 不带 arc_phase', () => {
    const card = {
      id: 'c4', text_id: 't4',
      character_arc: { phases: [{}, {}], selectable: false },
    }
    useAppStore.getState().setArcPhase('c4', 1)
    expect(useAppStore.getState().startSessionBody(card).arc_phase).toBeNull()
  })
})

describe('U12 身份赋值单一出口', () => {
  it('恢复存档：身份与阶段都取自 session', async () => {
    useAppStore.setState({ pendingCard: { id: 'c1', card_id: 'c1' } })
    await useAppStore.getState().enterArchive({
      id: 's1', card_id: 'c1', user_role: '存档的身份', arc_phase: 2,
    })
    expect(useAppStore.getState().sessionUserRole).toBe('存档的身份')
    expect(useAppStore.getState().sessionArcPhase).toBe(2)
  })

  it('新建：用卡片默认（最后阶段）', () => {
    useAppStore.getState().applySessionIdentity({
      user_role: useAppStore.getState().getUserRole('c1'),
      arc_phase: useAppStore.getState().defaultArcPhase(card3),
    })
    expect(useAppStore.getState().sessionArcPhase).toBe(3)
  })

  it('无阶段卡：默认阶段为 null', () => {
    expect(useAppStore.getState().defaultArcPhase({ id: 'c2' })).toBeNull()
  })
})
