/**
 * E9 阶段单选只在卡片可按时渲染：`selectable` 为真才出现；假 / 缺省都不渲染、不带 arc_phase。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
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

describe('E9 RoleSetupModal 只在 selectable 时渲染阶段单选', () => {
  it('selectable=true：渲染单选', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" arcPhases={PHASES}
        selectable onConfirm={() => {}} />)
    expect(container.querySelectorAll('input[type="radio"]').length).toBe(3)
  })

  it('selectable=false：即使有阶段也不渲染单选', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" arcPhases={PHASES}
        selectable={false} onConfirm={() => {}} />)
    expect(container.querySelectorAll('input[type="radio"]').length).toBe(0)
  })

  it('缺省 selectable（旧调用）：不渲染', () => {
    const { container } = render(
      <RoleSetupModal isOpen characterName="甲" characterId="c1" arcPhases={PHASES}
        onConfirm={() => {}} />)
    expect(container.querySelectorAll('input[type="radio"]').length).toBe(0)
  })
})
