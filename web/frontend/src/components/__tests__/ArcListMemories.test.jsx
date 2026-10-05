/**
 * F5 卡片展示：ArcList 在每个阶段下显示该阶段的 memories。
 * 无 memories 的阶段不渲染空块。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ArcList from '../common/ArcList'

const ARC = {
  axis: '',
  phases: [
    { label: '冷', state: '起初', memories: ['只在早期成立的事', '还有一件'] },
    { label: '热', state: '后来', memories: [] },
  ],
}

describe('F5 ArcList 阶段记忆', () => {
  it('把阶段 memories 显示在该阶段下', () => {
    const { container } = render(<ArcList arc={ARC} />)
    const items = [...container.querySelectorAll('.card-arc-item')]
    expect(items.length).toBe(2)
    const mems = [...items[0].querySelectorAll('.card-arc-memory')].map((el) => el.textContent)
    expect(mems).toEqual(['只在早期成立的事', '还有一件'])
  })

  it('没有 memories 的阶段不渲染记忆块', () => {
    const { container } = render(<ArcList arc={ARC} />)
    const items = [...container.querySelectorAll('.card-arc-item')]
    expect(items[1].querySelector('.card-arc-memories')).toBeNull()
  })
})
