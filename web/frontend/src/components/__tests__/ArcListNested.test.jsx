/**
 * spec arc-phase-unlocated §4 段 1：阶段 overlay 与卡片同形，ArcList 走嵌套叶子、拼回路径查标签。
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import ArcList from '../common/ArcList'

describe('ArcList 同形 overlay', () => {
  it('嵌套叶子拼回路径查标签：speaking_style.catchphrases → 口头禅', () => {
    const arc = { axis: '', phases: [{ label: '早', state: 's', overlay: {
      speaking_style: { catchphrases: ['哼'] }, cognitive: { knowledge_scope: '只识几个字' } } }] }
    const { container } = render(<ArcList arc={arc} />)
    const labels = [...container.querySelectorAll('.card-arc-overlay-label')].map((n) => n.textContent)
    expect([labels, container.textContent.includes('哼'), container.textContent.includes('只识几个字')])
      .toEqual([['口头禅', '见识范围'], true, true])
  })

  it('认不出的嵌套路径显示拼回的原名，不静默丢', () => {
    const arc = { axis: '', phases: [{ label: '早', state: 's', overlay: { mystery: { leaf: ['未知'] } } }] }
    const { container } = render(<ArcList arc={arc} />)
    expect(container.querySelector('.card-arc-overlay-label').textContent).toBe('mystery.leaf')
  })
})
