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

describe('B3 当前值标记', () => {
  // A8 之后 aria-selected 跟的是键盘高亮项，「当前值」改用 .is-current 标 —— 否则
  // 展开瞬间（高亮=当前值）两者同值，测不出区别。
  it('当前值带 is-current，其余不带', () => {
    setup({ value: 'guest' })
    const opts = screen.getAllByRole('option')
    expect(opts.map((o) => o.classList.contains('is-current'))).toEqual([false, false, true])
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

  it('收起时按 Space 展开，且 preventDefault', () => {
    const onChange = vi.fn()
    render(<Select value="user" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    trigger.focus()
    const ev = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
    fireEvent(trigger, ev)
    expect(ev.defaultPrevented).toBe(true)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
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

  // Space 与 Enter 同效。不 preventDefault 的话，按钮会在 keyup 触发原生 click ——
  // 只会把弹层关掉，选不中。
  it('展开后按 Space 选中高亮项并关闭，且 preventDefault', () => {
    const { onChange, trigger } = setup({ value: 'user' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const ev = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
    fireEvent(trigger, ev)
    expect(ev.defaultPrevented).toBe(true)
    expect(onChange).toHaveBeenCalledWith('guest')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('触发器 aria-activedescendant 跟随高亮，收起后消失', () => {
    const { trigger } = setup({ value: 'user' })
    const opts = screen.getAllByRole('option')
    expect(trigger).toHaveAttribute('aria-activedescendant', opts[1].id)
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(trigger).toHaveAttribute('aria-activedescendant', opts[2].id)
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(trigger).not.toHaveAttribute('aria-activedescendant')
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

  // 真实点击序列里触发器也有 mousedown。若它被当成「外部点击」，弹层先关；随后的
  // click 看到 open=false 又调 openMenu —— 结果点一下像没反应。
  it('展开后再点触发器会关闭（真实 mousedown+click 序列）', () => {
    const { trigger } = setup()
    fireEvent.mouseDown(trigger)
    fireEvent.click(trigger)
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

  // jsdom 没实现 scrollIntoView，先自己塞一个记录器（不用 vi.spyOn，属性本就不存在）。
  it('↓ 后在新高亮项上调用 scrollIntoView({ block: nearest })', () => {
    const seen = []
    Element.prototype.scrollIntoView = function (arg) {
      seen.push([this, arg])
    }
    try {
      const onChange = vi.fn()
      render(<Select value="t0" options={MANY} onChange={onChange} />)
      const trigger = screen.getByRole('combobox')
      trigger.getBoundingClientRect = () => rect()
      fireEvent.click(trigger)
      seen.length = 0 // 展开时高亮初值也算一次，只看 ↓ 这一次
      fireEvent.keyDown(trigger, { key: 'ArrowDown' })

      const opts = screen.getAllByRole('option')
      expect(seen).toHaveLength(1)
      expect(seen[0][0]).toBe(opts[1])
      expect(seen[0][1]).toEqual({ block: 'nearest' })
    } finally {
      delete Element.prototype.scrollIntoView
    }
  })
})

describe('A1–A8 对齐 APG 补齐的行为', () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ value: `t${i}`, label: `项${i}` }))

  // 收起时按 ↑ 的高亮落点必须与 ↓ 不同：↓ 落在当前值，↑ 落首项。
  it('A1 收起时按 ↑ 展开并高亮首项', () => {
    const onChange = vi.fn()
    render(<Select value="guest" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowUp' })
    expect(screen.getAllByRole('option')[0].className).toContain('is-active')
  })

  it('A2 收起按 End 高亮末项，展开后按 Home 高亮首项', () => {
    const onChange = vi.fn()
    render(<Select value="admin" options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'End' })
    expect(screen.getAllByRole('option')[2].className).toContain('is-active')
    fireEvent.keyDown(trigger, { key: 'Home' })
    expect(screen.getAllByRole('option')[0].className).toContain('is-active')
  })

  it('A3 键入跳转：连按同字符在候选间循环，500ms 后缓冲清空', () => {
    const AB = [
      { value: 'a1', label: '甲一' },
      { value: 'b1', label: '乙一' },
      { value: 'b2', label: '乙二' },
    ]
    vi.useFakeTimers()
    try {
      const onChange = vi.fn()
      render(<Select value="" options={AB} onChange={onChange} />)
      const trigger = screen.getByRole('combobox')
      trigger.getBoundingClientRect = () => rect()
      trigger.focus()

      fireEvent.keyDown(trigger, { key: '乙' })
      expect(screen.getAllByRole('option')[1].className).toContain('is-active')
      fireEvent.keyDown(trigger, { key: '乙' })
      expect(screen.getAllByRole('option')[2].className).toContain('is-active')

      vi.advanceTimersByTime(600)
      fireEvent.keyDown(trigger, { key: '甲' })
      expect(screen.getAllByRole('option')[0].className).toContain('is-active')
    } finally {
      vi.useRealTimers()
    }
  })

  // APG 的 onComboType 第一行就是 updateMenuState(true)：没有匹配也要展开，否则用户
  // 打错一个字会以为组件没反应。清空缓冲同理 —— 死字符留在串里，后面每一下都拼不上。
  it('A3 无匹配也展开，并立刻清空缓冲（下一键不拼成两字串）', () => {
    const AB = [
      { value: 'a1', label: '甲一' },
      { value: 'b1', label: '乙一' },
      { value: 'b2', label: '乙二' },
    ]
    const onChange = vi.fn()
    render(<Select value="b1" options={AB} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    trigger.focus()

    fireEvent.keyDown(trigger, { key: '丙' })
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getAllByRole('option')[1].className).toContain('is-active')

    fireEvent.keyDown(trigger, { key: '乙' })
    expect(screen.getAllByRole('option')[2].className).toContain('is-active')
  })

  // 整串匹配也从高亮项之后开始绕回：高亮在 ab1 时连打 a、b，a 先绕到 ab2，ab 再从
  // ab2 之后绕到 ab3。若整串从 0 开始找，会一直停在 ab1。
  it('A3 整串匹配从高亮项之后开始绕回', () => {
    const AB3 = ['ab1', 'ab2', 'ab3'].map((s) => ({ value: s, label: s }))
    const onChange = vi.fn()
    render(<Select value="ab1" options={AB3} onChange={onChange} />)
    const trigger = screen.getByRole('combobox')
    trigger.getBoundingClientRect = () => rect()
    fireEvent.click(trigger)

    fireEvent.keyDown(trigger, { key: 'a' })
    expect(screen.getAllByRole('option')[1].className).toContain('is-active')
    fireEvent.keyDown(trigger, { key: 'b' })
    expect(screen.getAllByRole('option')[2].className).toContain('is-active')
  })

  it('A4 展开后按 Tab 选中高亮项，且不 preventDefault', () => {
    const { onChange, trigger } = setup({ value: 'user' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    fireEvent(trigger, ev)
    expect(ev.defaultPrevented).toBe(false)
    expect(onChange).toHaveBeenCalledWith('guest')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('A5 展开后按 Alt+↑ 选中高亮项并关闭', () => {
    const { onChange, trigger } = setup({ value: 'user' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'ArrowUp', altKey: true })
    expect(onChange).toHaveBeenCalledWith('guest')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('A6 PageDown 一次跳 10 项，末项封顶', () => {
    const onChange = vi.fn()
    const first = render(<Select value="t0" options={many} onChange={onChange} />)
    const t0 = screen.getByRole('combobox')
    t0.getBoundingClientRect = () => rect()
    fireEvent.click(t0)
    fireEvent.keyDown(t0, { key: 'PageDown' })
    expect(screen.getAllByRole('option')[10].className).toContain('is-active')
    first.unmount()

    render(<Select value="t25" options={many} onChange={onChange} />)
    const t25 = screen.getByRole('combobox')
    t25.getBoundingClientRect = () => rect()
    fireEvent.click(t25)
    fireEvent.keyDown(t25, { key: 'PageDown' })
    expect(screen.getAllByRole('option')[29].className).toContain('is-active')
  })

  it('A6 PageUp 一次跳 10 项，首项封顶', () => {
    const onChange = vi.fn()
    const first = render(<Select value="t25" options={many} onChange={onChange} />)
    const t25 = screen.getByRole('combobox')
    t25.getBoundingClientRect = () => rect()
    fireEvent.click(t25)
    fireEvent.keyDown(t25, { key: 'PageUp' })
    expect(screen.getAllByRole('option')[15].className).toContain('is-active')
    first.unmount()

    render(<Select value="t4" options={many} onChange={onChange} />)
    const t4 = screen.getByRole('combobox')
    t4.getBoundingClientRect = () => rect()
    fireEvent.click(t4)
    fireEvent.keyDown(t4, { key: 'PageUp' })
    expect(screen.getAllByRole('option')[0].className).toContain('is-active')
  })

  it('A7 aria-controls 指向 listbox，收起后消失', () => {
    const { trigger } = setup()
    const listbox = screen.getByRole('listbox')
    expect(trigger).toHaveAttribute('aria-controls', listbox.id)
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(trigger).not.toHaveAttribute('aria-controls')
  })

  it('A8 aria-selected 只在高亮项上，is-current 留在当前值', () => {
    const { trigger } = setup({ value: 'user' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const opts = screen.getAllByRole('option')
    expect(opts.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true'])
    expect(opts.map((o) => o.classList.contains('is-current'))).toEqual([false, true, false])
  })
})

describe('B8 卸载清理', () => {
  it('展开期间注册的监听在卸载时逐对移除（handler 引用一致）', () => {
    const addDoc = vi.spyOn(document, 'addEventListener')
    const remDoc = vi.spyOn(document, 'removeEventListener')
    const addWin = vi.spyOn(window, 'addEventListener')
    const remWin = vi.spyOn(window, 'removeEventListener')
    try {
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
    } finally {
      // spy 不恢复会泄漏到本文件后续用例（它们也吃 document/window 上的监听）。
      addDoc.mockRestore()
      remDoc.mockRestore()
      addWin.mockRestore()
      remWin.mockRestore()
    }
  })
})
