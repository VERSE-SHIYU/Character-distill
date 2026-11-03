import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import StackSplit from '../common/StackSplit'

// jsdom 不排版：容器的位置与高度用固定值代替（top 0、高 400）
const RECT = { top: 0, left: 0, width: 240, height: 400, right: 240, bottom: 400, x: 0, y: 0 }

const setWidth = (w) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: w })
  window.matchMedia = (q) => ({
    matches: q.includes('max-width') && w <= 768, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
  })
}

const renderSplit = () => {
  const view = render(<StackSplit top={<p>卡片</p>} bottom={<p>名单</p>} storageKey="t_split" />)
  const box = view.container.querySelector('.stack-split')
  if (box) box.getBoundingClientRect = () => RECT
  return view
}

const pane = (c, which) => c.querySelector(`[data-pane="${which}"]`)

const drag = (y) => act(() => {
  fireEvent.pointerDown(screen.getByRole('separator'))
  fireEvent.pointerMove(document, { clientY: y })
  fireEvent.pointerUp(document)
})

beforeEach(() => {
  localStorage.clear()
  setWidth(1280)
})

describe('StackSplit：上下分栏、可拖动', () => {
  it('桌面：两栏都渲染，中间有分隔条，默认上栏 0.4', () => {
    const { container } = renderSplit()
    expect(screen.getByText('卡片')).toBeTruthy()
    expect(screen.getByText('名单')).toBeTruthy()
    expect(screen.getByRole('separator')).toBeTruthy()
    expect(pane(container, 'top').style.flex).toMatch(/^0\.4/)
    expect(pane(container, 'bottom').style.flex).toMatch(/^0\.6/)
  })

  it('拖到 300px（容器高 400）：上栏 0.75、下栏 0.25，并记住位置', () => {
    const { container } = renderSplit()
    drag(300)
    expect(pane(container, 'top').style.flex).toMatch(/^0\.75/)
    expect(pane(container, 'bottom').style.flex).toMatch(/^0\.25/)
    expect(localStorage.getItem('t_split')).toBe('0.75')
  })

  it('拖到最顶 / 最底：夹在 0.15–0.85，任何一栏都不会被挤没', () => {
    const { container } = renderSplit()
    drag(0)
    expect(pane(container, 'top').style.flex).toMatch(/^0\.15/)
    drag(400)
    expect(pane(container, 'top').style.flex).toMatch(/^0\.85/)
  })

  it('松手后再移动指针不再改比例', () => {
    const { container } = renderSplit()
    drag(300)
    act(() => { fireEvent.pointerMove(document, { clientY: 100 }) })
    expect(pane(container, 'top').style.flex).toMatch(/^0\.75/)
  })

  it('下次打开沿用上次拖的位置', () => {
    localStorage.setItem('t_split', '0.3')
    const { container } = renderSplit()
    expect(pane(container, 'top').style.flex).toMatch(/^0\.3/)
  })

  it('手机：不分栏、没有分隔条，两块顺排', () => {
    setWidth(375)
    renderSplit()
    expect(screen.getByText('卡片')).toBeTruthy()
    expect(screen.getByText('名单')).toBeTruthy()
    expect(screen.queryByRole('separator')).toBeNull()
  })
})
