import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SplitLayout, { PaneToggle } from '../SplitLayout'

// 收起列表机制：容器状态、记忆、空状态兜底、移动端不生效。
// 这里只测状态机与语义；「列表真的消失、对话区真的变宽」jsdom 不加载 CSS，交给 e2e/split-layout-verify.cjs。

const { mobile } = vi.hoisted(() => ({ mobile: { value: false } }))
vi.mock('../../../hooks/useIsMobile', () => ({ default: () => mobile.value }))

const KEY = (id) => `split_list_collapsed:${id}`

function Page({ id = 'x', hasActive = true, label = '会话列表' }) {
  return (
    <SplitLayout id={id} label={label} hasActive={hasActive} list={<aside>列表</aside>} className="pg">
      <main><header><PaneToggle /></header></main>
    </SplitLayout>
  )
}
const root = (c) => c.querySelector('.split-layout')

describe('SplitLayout', () => {
  beforeEach(() => { localStorage.clear(); mobile.value = false; vi.restoreAllMocks() })

  it('S1 点开关：aria-expanded 与容器状态同步翻转', () => {
    const { container } = render(<Page />)
    const btn = screen.getByRole('button', { name: '会话列表' })
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    expect(root(container).dataset.listCollapsed).toBe('false')
    fireEvent.click(btn)
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    expect(root(container).dataset.listCollapsed).toBe('true')
    fireEvent.click(btn)
    expect(root(container).dataset.listCollapsed).toBe('false')
  })

  it('S2 状态写入 localStorage，重新挂载后恢复', () => {
    const first = render(<Page />)
    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    expect(localStorage.getItem(KEY('x'))).toBe('1')
    first.unmount()
    const { container } = render(<Page />)
    expect(root(container).dataset.listCollapsed).toBe('true')
  })

  it('S3 没有打开的对话时，已保存的收起状态不生效（否则开关不可达）', () => {
    localStorage.setItem(KEY('x'), '1')
    const { container } = render(<Page hasActive={false} />)
    expect(root(container).dataset.listCollapsed).toBe('false')
  })

  it('S4 移动端：不渲染按钮，已保存的收起状态也不生效', () => {
    mobile.value = true
    localStorage.setItem(KEY('x'), '1')
    const { container } = render(<Page />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(root(container).dataset.listCollapsed).toBe('false')
  })

  it('S5 不在 SplitLayout 内时 PaneToggle 什么都不渲染', () => {
    const { container } = render(<PaneToggle />)
    expect(container.innerHTML).toBe('')
  })

  it('S6 名称固定为 label，title 随状态变化', () => {
    render(<Page label="群聊列表" />)
    const btn = screen.getByRole('button', { name: '群聊列表' })
    expect(btn.getAttribute('title')).toBe('收起群聊列表')
    fireEvent.click(btn)
    expect(btn.getAttribute('aria-label')).toBe('群聊列表')
    expect(btn.getAttribute('title')).toBe('展开群聊列表')
  })

  it('S7 每页各存一份，互不影响', () => {
    localStorage.setItem(KEY('chat'), '1')
    const { container } = render(<Page id="dm" />)
    expect(root(container).dataset.listCollapsed).toBe('false')
  })

  it('S8 localStorage 读写抛错时不崩，仍可切换', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    const { container } = render(<Page />)
    fireEvent.click(screen.getByRole('button', { name: '会话列表' }))
    expect(root(container).dataset.listCollapsed).toBe('true')
  })
})
