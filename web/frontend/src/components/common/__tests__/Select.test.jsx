import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Select from '../Select'

// Radix 版 Select 的封装层测试：只测我们自己的空值映射与属性透传。键盘导航、读屏语义、
// 键入跳转、焦点管理、定位计算都是库的行为，不在这里重测。
// 打开下拉走键盘 Enter —— Radix 的 OPEN_KEYS 是 [' ', 'Enter', 'ArrowUp', 'ArrowDown']。

const OPTIONS = [
  { value: '', label: '全部文本' },
  { value: '7', label: '文本A.txt' },
  { value: '9', label: '文本B.txt' },
]

function setup(props = {}) {
  const onChange = vi.fn()
  const utils = render(
    <div data-testid="wrap">
      <Select value="" options={OPTIONS} onChange={onChange} ariaLabel="文本筛选" {...props} />
    </div>,
  )
  return { onChange, trigger: screen.getByRole('combobox', { name: '文本筛选' }), ...utils }
}

const open = (trigger) => fireEvent.keyDown(trigger, { key: 'Enter' })

describe('Radix Select 封装层', () => {
  // Radix 把 '' 当「未选中」显示 placeholder（触发器文字为空），而调用方的「全部」项取值
  // 正是 ''。封装层把 '' 与哨兵值互相映射，所以这里显示的必须是该项的 label。
  it('W1 值为空串时触发器显示该项 label，而不是 Radix 的占位态', () => {
    const { trigger } = setup()
    expect(trigger.textContent).toContain('全部文本')
    expect(trigger).not.toHaveAttribute('data-placeholder')
  })

  it('W2 选「全部文本」时 onChange 收到空串', () => {
    const { onChange, trigger } = setup({ value: '7' })
    open(trigger)
    fireEvent.click(screen.getByRole('option', { name: '全部文本' }))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('W3 弹层 portal 到 body，不在调用方容器内', () => {
    const { container, trigger } = setup()
    open(trigger)
    const listbox = screen.getByRole('listbox')
    expect(container.contains(listbox)).toBe(false)
    expect(document.body.contains(listbox)).toBe(true)
  })

  it('W4 传 disabled 后触发器禁用', () => {
    const { trigger } = setup({ disabled: true })
    expect(trigger).toBeDisabled()
  })

  it('W5 size 与 className 落到触发器上', () => {
    const { trigger } = setup({ size: 'sm', className: 'my-select' })
    expect(trigger.className).toContain('ui-select--sm')
    expect(trigger.className).toContain('my-select')
  })

  it('W6 弹层用 popper 定位（data-side 为 bottom）', () => {
    const { trigger } = setup()
    open(trigger)
    expect(screen.getByRole('listbox')).toHaveAttribute('data-side', 'bottom')
  })
})
