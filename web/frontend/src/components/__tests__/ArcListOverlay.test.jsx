/**
 * E10 弧线列表只读展示每阶段的 overlay：字段用中文标签，未知键显原名；值是列表时逐条列出。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ArcList from '../common/ArcList'

const ARC = {
  axis: '',
  phases: [{
    label: '冷',
    state: '起初他谁都不信',
    overlay: {
      personality_traits: ['疑心重'],
      'speaking_style.catchphrases': ['哼'],
      mystery_field: ['未知内容'],
    },
  }],
}

describe('E10 ArcList 阶段 overlay', () => {
  it('逐字段列出阶段特有内容', () => {
    const { container } = render(<ArcList arc={ARC} />)
    const text = container.textContent
    expect(text).toContain('疑心重')
    expect(text).toContain('哼')
    expect(text).toContain('未知内容')
  })

  it('未知键显示原名', () => {
    const { container } = render(<ArcList arc={ARC} />)
    expect(container.textContent).toContain('mystery_field')
  })

  it('无 overlay：不渲染 overlay 区块', () => {
    const { container } = render(
      <ArcList arc={{ axis: '', phases: [{ label: '冷', state: '起初' }] }} />)
    expect(container.querySelector('.card-arc-overlay')).toBeNull()
  })
})
