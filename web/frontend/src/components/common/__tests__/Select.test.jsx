import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Select from '../Select'

const OPTIONS = [
  { value: 'admin', label: '管理员' },
  { value: 'user', label: '用户' },
  { value: 'guest', label: '演示（只读）' },
]

const rect = (over = {}) => ({
  width: 140, height: 40, top: 100, left: 20, right: 160, bottom: 140, x: 20, y: 100, ...over,
})

// 触发器 rect 必须 mock：jsdom 的 getBoundingClientRect 全是 0，不加就是「贴视口顶、
// 宽 0」，翻转和宽度两条都测不出东西。
function setup(props = {}) {
  const onChange = vi.fn()
  const utils = render(
    <div data-testid="wrap">
      <Select value="user" options={OPTIONS} onChange={onChange} ariaLabel="角色" {...props} />
    </div>,
  )
  const trigger = screen.getByRole('combobox')
  trigger.getBoundingClientRect = () => rect()
  fireEvent.click(trigger)
  return { onChange, trigger, ...utils }
}

describe('B1 触发器语义', () => {
  it('是 button，带 combobox/haspopup，显示当前 label', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} ariaLabel="角色" />)
    const trigger = screen.getByRole('combobox')
    expect(trigger.tagName).toBe('BUTTON')
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger.textContent).toBe('用户')
  })
})

describe('B2 弹层定位', () => {
  it('portal 到 body，不在调用方容器内', () => {
    const { container } = setup()
    const listbox = screen.getByRole('listbox')
    expect(container.contains(listbox)).toBe(false)
    expect(listbox.parentElement).toBe(document.body)
  })

  it('宽度不小于触发器', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect({ width: 200 })
    fireEvent.click(trigger)
    expect(screen.getByRole('listbox').style.minWidth).toBe('200px')
  })

  it('下方空间不够时向上翻', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect({ top: 700, bottom: 740 })
    fireEvent.click(trigger)
    const menu = screen.getByRole('listbox')
    expect(menu.style.bottom).not.toBe('')
    expect(menu.style.top).toBe('')
  })

  it('空间充足时向下展开', () => {
    setup()
    const menu = screen.getByRole('listbox')
    expect(menu.style.top).not.toBe('')
    expect(menu.style.bottom).toBe('')
  })
})

describe('B3 选中态', () => {
  it('当前值 aria-selected=true，其余 false', () => {
    setup({ value: 'guest' })
    const opts = screen.getAllByRole('option')
    expect(opts.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true'])
  })
})

describe('B4 选中行为', () => {
  it('选新值触发一次 onChange 并关闭', () => {
    const { onChange } = setup()
    fireEvent.click(screen.getByRole('option', { name: '管理员' }))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('admin')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('选当前值只关闭，不触发', () => {
    const { onChange } = setup()
    fireEvent.click(screen.getByRole('option', { name: '用户' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  // 真实点击是 mousedown -> mouseup -> click。选项上的 mousedown 不能被当成「外部点击」
  // 先关掉弹层，否则随后的 click 落在已卸载的节点上，永远选不中。
  it('点选项时 mousedown 不抢先关闭（真实点击序列）', () => {
    const { onChange } = setup()
    const opt = screen.getByRole('option', { name: '管理员' })
    fireEvent.mouseDown(opt)
    fireEvent.click(opt)
    expect(onChange).toHaveBeenCalledWith('admin')
  })
})

describe('B5 键盘', () => {
  it('聚焦后按 ↓ 展开，高亮落在当前值', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getAllByRole('option')[1].className).toContain('is-active')
  })

  it('↓↓ 移动后 Enter 选中对应值', () => {
    const { onChange, trigger } = setup({ value: 'admin' })
    // 展开时高亮在 admin(0)，两次 ↓ 到 guest(2)
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('guest')
  })

  it('末项按 ↓ 不循环', () => {
    const { trigger } = setup({ value: 'guest' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[2].className).toContain('is-active')
  })

  it('首项按 ↑ 不循环', () => {
    const { trigger } = setup({ value: 'admin' })
    fireEvent.keyDown(trigger, { key: 'ArrowUp' })
    expect(screen.getAllByRole('option')[0].className).toContain('is-active')
  })

  it('Esc 关闭并把焦点交还触发器', () => {
    const { trigger } = setup()
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('Tab 关闭', () => {
    const { trigger } = setup()
    fireEvent.keyDown(trigger, { key: 'Tab' })
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('B6 展开期间的关闭来源', () => {
  it('外部 mousedown 关闭', () => {
    setup()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('容器内滚动关闭（捕获阶段到 document）', () => {
    const { container } = setup()
    fireEvent.scroll(container.firstChild)
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('window resize 关闭', () => {
    setup()
    fireEvent.resize(window)
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('B7 disabled', () => {
  it('点击与 ↓ 都不展开，触发器带 disabled 属性', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} disabled />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    expect(trigger).toBeDisabled()
    fireEvent.click(trigger)
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('B9 长列表', () => {
  const MANY = Array.from({ length: 30 }, (_, i) => ({ value: `t${i}`, label: `很长的文本文件名 ${i}` }))

  it('弹层封顶可滚，内部滚动不关闭，选项带完整 title', () => {
    const onChange = vi.fn()
    render(<Select value="t0" options={MANY} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    fireEvent.click(trigger)

    const menu = screen.getByRole('listbox')
    expect(menu.style.maxHeight).toBe('280px')
    expect(menu.style.overflowY).toBe('auto')
    expect(screen.getAllByRole('option')).toHaveLength(30)
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('title', '很长的文本文件名 0')

    fireEvent.scroll(menu)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('估高按 280 封顶：下方只剩 300px 时仍向下展开', () => {
    const onChange = vi.fn()
    render(<Select value="t0" options={MANY} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    const bottom = window.innerHeight - 300
    trigger.getBoundingClientRect = () => rect({ top: bottom - 40, bottom })
    fireEvent.click(trigger)

    const menu = screen.getByRole('listbox')
    expect(menu.style.top).not.toBe('')
    expect(menu.style.bottom).toBe('')
  })
})

describe('B8 卸载清理', () => {
  it('展开期间注册的监听在卸载时逐对移除（handler 引用一致）', () => {
    const addDoc = vi.spyOn(document, 'addEventListener')
    const remDoc = vi.spyOn(document, 'removeEventListener')
    const addWin = vi.spyOn(window, 'addEventListener')
    const remWin = vi.spyOn(window, 'removeEventListener')
    const baseDoc = addDoc.mock.calls.length
    const baseWin = addWin.mock.calls.length

    const onChange = vi.fn()
    const { unmount } = render(<Select value="user" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    fireEvent.click(trigger)
    expect(screen.getByRole('listbox')).toBeInTheDocument()

    const ours = [
      ...addDoc.mock.calls.slice(baseDoc).map(([type, handler]) => ({ rem: remDoc.mock.calls, type, handler })),
      ...addWin.mock.calls.slice(baseWin).map(([type, handler]) => ({ rem: remWin.mock.calls, type, handler })),
    ].filter((c) => ['mousedown', 'scroll', 'resize'].includes(c.type))

    expect(ours.map((c) => c.type).sort()).toEqual(['mousedown', 'resize', 'scroll'])

    unmount()

    for (const c of ours) {
      const removed = c.rem.some(([type, handler]) => type === c.type && handler === c.handler)
      expect(removed, `未移除监听：${c.type}`).toBe(true)
    }
  })
})
