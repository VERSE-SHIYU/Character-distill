import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ArcList from '../ArcList'

const texts = (c) => [...c.querySelectorAll('.card-arc-item .card-arc-text')].map((el) => el.textContent)

describe('ArcList', () => {
  it('新卡：显示变化轴，阶段显示「心态 · 状态」并带序号', () => {
    const { container } = render(<ArcList arc={{
      axis: '从桀骜到担当',
      phases: [{ label: '桀骜不服', state: '大闹天宫前后，动辄动手' }, { label: '护师担当', state: '取经路上' }],
    }} />)
    expect(container.querySelector('.card-arc-axis').textContent).toBe('从桀骜到担当')
    expect(texts(container)).toEqual(['桀骜不服 · 大闹天宫前后，动辄动手', '护师担当 · 取经路上'])
    expect([...container.querySelectorAll('.card-arc-index')].map((el) => el.textContent)).toEqual(['1', '2'])
  })

  it('旧卡：没有轴就不渲染轴那一行，没有 label 的阶段只显示状态', () => {
    const { container } = render(<ArcList arc={{ axis: '', phases: [{ label: '', state: '起初冷漠' }] }} />)
    expect(container.querySelector('.card-arc-axis')).toBeNull()
    expect(texts(container)).toEqual(['起初冷漠'])
    expect(container.querySelector('.card-arc-label')).toBeNull()
  })
})
