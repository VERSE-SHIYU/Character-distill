/**
 * U13 身份弹窗的阶段单选：有阶段卡渲染原生单选项、默认最后阶段；无阶段不渲染。
 * 选中某项 → setArcPhase(cardId, k)。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import RoleSetupModal from '../RoleSetupModal'

const { store } = vi.hoisted(() => {
  const s = {
    getUserRole: vi.fn(() => ''),
    setUserRole: vi.fn(),
    setSessionUserRole: vi.fn(),
    getArcPhase: vi.fn(() => null),
    setArcPhase: vi.fn(),
  }
  return { store: s }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(store)
  hook.getState = () => store
  return { default: hook }
})

const PHASES = [
  { label: '冷', state: '起初他谁都不信' },
  { label: '中', state: '渐渐放下戒心' },
  { label: '热', state: '后来可以交命' },
]

beforeEach(() => {
  vi.clearAllMocks()
  store.getArcPhase.mockReturnValue(null)
})

describe('U13 RoleSetupModal 阶段单选', () => {
  it('有阶段：渲染单选、默认选中最后阶段', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" arcPhases={PHASES} onConfirm={() => {}} />)
    const radios = container.querySelectorAll('input[type="radio"]')
    expect(radios.length).toBe(3)
    expect(radios[2].checked).toBe(true)
  })

  it('选中阶段 1 → setArcPhase(cardId, 1)', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" arcPhases={PHASES} onConfirm={() => {}} />)
    fireEvent.click(container.querySelectorAll('input[type="radio"]')[0])
    expect(store.setArcPhase).toHaveBeenCalledWith('c1', 1)
  })

  it('无阶段卡：不渲染单选', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" onConfirm={() => {}} />)
    expect(container.querySelectorAll('input[type="radio"]').length).toBe(0)
  })
})
