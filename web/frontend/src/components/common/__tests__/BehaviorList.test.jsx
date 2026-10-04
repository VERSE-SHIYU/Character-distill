import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import BehaviorList from '../BehaviorList'

describe('BehaviorList', () => {
  it('每条显示情境与做法；有原文摘录才显示摘录', () => {
    const { container } = render(<BehaviorList items={[
      { situation: '被人当众质疑', behavior: '先反问对方凭什么', source_quote: '你凭什么这么说' },
      { situation: '被人取笑', behavior: '跟着自嘲', source_quote: '' },
    ]} />)
    const items = [...container.querySelectorAll('.card-behavior-item')]
    expect(items.map((li) => li.querySelector('.card-behavior-situation').textContent)).toEqual(['被人当众质疑', '被人取笑'])
    expect(items.map((li) => li.querySelector('.card-behavior-text').textContent)).toEqual(['先反问对方凭什么', '跟着自嘲'])
    expect(items[0].querySelector('.card-behavior-quote').textContent).toBe('你凭什么这么说')
    expect(items[1].querySelector('.card-behavior-quote')).toBeNull()
  })
})
