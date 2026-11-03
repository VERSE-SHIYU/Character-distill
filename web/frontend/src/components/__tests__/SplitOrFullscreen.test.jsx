import { describe, it, expect, beforeEach } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import SplitOrFullscreen from '../common/SplitOrFullscreen'

// jsdom 不排版：容器的位置与宽度用固定值代替（left 0、宽 1000）
const RECT = { top: 0, left: 0, width: 1000, height: 600, right: 1000, bottom: 600, x: 0, y: 0 }

beforeEach(() => {
  localStorage.clear()
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
  window.matchMedia = (q) => ({
    matches: false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
  })
})

const renderSplit = (props = {}) => {
  const view = render(
    <SplitOrFullscreen open main={<p>对话</p>} panel={<p>历史</p>} storageKey="t_chat_split" {...props} />)
  view.container.firstChild.getBoundingClientRect = () => RECT
  return view
}
const mainPane = (c) => c.firstChild.firstChild
const drag = (c, x) => act(() => {
  fireEvent.pointerDown(c.querySelector('.chat-splitter'))
  fireEvent.pointerMove(document, { clientX: x })
  fireEvent.pointerUp(document)
})

describe('SplitOrFullscreen：左右分隔条真的能拖（11-03 前三处调用都传空函数，拖不动）', () => {
  it('默认比例来自 defaultRatio', () => {
    const { container } = renderSplit({ defaultRatio: 0.7 })
    expect(mainPane(container).style.flex).toMatch(/^0\.7/)
  })

  it('拖到 500px（容器宽 1000）：主栏变 0.5，并记住位置', () => {
    const { container } = renderSplit()
    drag(container, 500)
    expect(mainPane(container).style.flex).toMatch(/^0\.5/)
    expect(localStorage.getItem('t_chat_split')).toBe('0.5')
  })

  it('夹在 0.4–0.8', () => {
    const { container } = renderSplit()
    drag(container, 0)
    expect(mainPane(container).style.flex).toMatch(/^0\.4/)
    drag(container, 1000)
    expect(mainPane(container).style.flex).toMatch(/^0\.8/)
  })

  it('下次打开沿用上次拖的位置', () => {
    localStorage.setItem('t_chat_split', '0.55')
    const { container } = renderSplit()
    expect(mainPane(container).style.flex).toMatch(/^0\.55/)
  })
})
