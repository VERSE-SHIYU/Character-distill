import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import ErrorBox from '../common/ErrorBox'

// 115：ErrorBox 把图标字形渲染成了字面 `??` 与 `?` —— 字形在入库前就被 ASCII 替换掉了
// （唯一的提交 ae47a216 里就已经是 `??`，文件全 ASCII）。现在改用 Icon.jsx 里自绘的 SVG，
// 字形与文件编码无关，同样的丢失不可能重演。
//
// 断言写「不含单个 `?`」而不是只查 `??`：变异若只把一处换回 `?`，只查 `??` 的断言不会红。

describe('115：ErrorBox 的图标是真字形，不是字面问号', () => {
  it('文案照常渲染，且不含任何 `?`', () => {
    const { container } = render(<ErrorBox message="游客无权发布到市场" />)

    const text = container.querySelector('.error-box').textContent
    expect(text).toContain('游客无权发布到市场')
    expect(text).not.toContain('?')
  })

  it('警告前缀是 AlertTriangle 的图形，不是占位字符', () => {
    const { container } = render(<ErrorBox message="出错了" />)

    const icon = container.querySelector('.error-box span svg')
    expect(icon).not.toBeNull()
    expect(icon.querySelector('path').getAttribute('d')).toContain('10.29 3.86')
  })

  it('关闭按钮是自绘的叉，带可读名称，点了会回调', () => {
    const onDismiss = vi.fn()
    const { container } = render(<ErrorBox message="出错了" onDismiss={onDismiss} />)

    const btn = container.querySelector('.error-box button')
    expect(btn.getAttribute('aria-label')).toBe('关闭')
    expect(btn.textContent).not.toContain('?')
    expect(btn.querySelector('svg')).not.toBeNull()

    fireEvent.click(btn)
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
