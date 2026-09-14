import { describe, it, expect } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import EvidenceRail from '../common/EvidenceRail'

const sceneHit = (text = '屋顶上的旧事，风很凉。') => ({
  source: 'scene', status: 'hit',
  items: [{ text, truncated: false, score: 0.7, meta: { chunk_id: 'scene_3' } }],
})

const summaryOf = (container) => container.querySelector('.evidence-summary')?.textContent

describe('EvidenceRail', () => {
  it('默认折叠：只给一行结论，正文不渲染', () => {
    const { container } = render(
      <EvidenceRail evidence={[sceneHit(), sceneHit('第二段'), { source: 'memory', status: 'hit', items: [{ text: '记得' }] }]} />
    )
    expect(summaryOf(container)).toBe('检索来源：2 段原文 · 1 条记忆')
    expect(container.querySelector('.evidence-body')).toBeNull()
    expect(container.textContent).not.toContain('屋顶上的旧事')
  })

  it('点开展开才给原文', () => {
    const { container } = render(<EvidenceRail evidence={[sceneHit()]} />)
    fireEvent.click(container.querySelector('.evidence-toggle'))
    expect(container.querySelector('.evidence-body')).not.toBeNull()
    expect(container.textContent).toContain('屋顶上的旧事，风很凉。')
  })

  it('命中态与三种未命中态的文案两两不同（压成一态即红）', () => {
    const seen = {}
    for (const status of ['hit', 'empty', 'failed', 'timeout']) {
      const { container, unmount } = render(
        <EvidenceRail evidence={[{ source: 'scene', status, items: status === 'hit' ? [{ text: 'a' }] : [] }]} />
      )
      seen[status] = summaryOf(container)
      unmount()
    }
    expect(seen.hit).toContain('1 段原文')
    expect(seen.empty).toContain('没查到相关内容')
    expect(seen.failed).toContain('检索失败')
    expect(seen.timeout).toContain('检索超时')
    // 有命中就不能等同于任何一种未命中；三种未命中彼此也必须不同
    expect(new Set(Object.values(seen)).size).toBe(4)
  })

  it('展开态说清是哪一路没查到，以及为什么', () => {
    const { container } = render(
      <EvidenceRail evidence={[{ source: 'web', status: 'timeout', items: [] }]} />
    )
    fireEvent.click(container.querySelector('.evidence-toggle'))
    const card = container.querySelector('.evidence-card')
    expect(card.textContent).toContain('网络')
    expect(card.querySelector('.evidence-card-status').textContent).toBe('检索超时')
  })

  it('混合态：有命中的同时另一路超时，谁都不吞谁', () => {
    const { container } = render(
      <EvidenceRail evidence={[sceneHit(), { source: 'web', status: 'timeout', items: [] }]} />
    )
    expect(summaryOf(container)).toBe('检索来源：1 段原文 · 检索超时')
  })

  it('被截断的预览显式标注（不许静默截断）', () => {
    const { container } = render(
      <EvidenceRail evidence={[{ source: 'scene', status: 'hit', items: [{ text: '甲'.repeat(300), truncated: true }] }]} />
    )
    fireEvent.click(container.querySelector('.evidence-toggle'))
    expect(container.textContent).toContain('（已截断）')
  })

  it('meta.url 有才渲染成链接，取不到就不渲染（不造空链接/死链）', () => {
    const withUrl = {
      source: 'web', status: 'hit',
      items: [{ text: '莲花坞', meta: { url: 'https://example.org/l', source: '示例百科' } }],
    }
    const withoutUrl = {
      source: 'web', status: 'hit',
      items: [{ text: '莲花坞', meta: { url: null, source: null } }],
    }
    const a = render(<EvidenceRail evidence={[withUrl]} />)
    fireEvent.click(a.container.querySelector('.evidence-toggle'))
    const link = a.container.querySelector('.evidence-item-link')
    expect(link.getAttribute('href')).toBe('https://example.org/l')
    expect(link.getAttribute('rel')).toContain('noopener')

    const b = render(<EvidenceRail evidence={[withoutUrl]} />)
    fireEvent.click(b.container.querySelector('.evidence-toggle'))
    expect(b.container.querySelector('.evidence-item-link')).toBeNull()
    expect(b.container.textContent).toContain('莲花坞')
  })

  // ── 降级路径：四条都不渲染一个节点，也都不抛错 ──
  it.each([
    ['老消息无关联证据（后端给 null）', null],
    ['legacy 路径压根没这个字段（undefined）', undefined],
    ['检索没发生过（空数组）', []],
  ])('%s：不渲染', (_name, evidence) => {
    const { container } = render(<EvidenceRail evidence={evidence} />)
    expect(container.querySelector('.evidence-rail')).toBeNull()
  })

  it('未知 kind / 未知 status：静默降级，不崩也不渲染', () => {
    const unknownKind = render(
      <EvidenceRail evidence={[{ source: 'video', status: 'hit', items: [{ text: 'x' }] }]} />
    )
    expect(unknownKind.container.querySelector('.evidence-rail')).toBeNull()

    const unknownStatus = render(
      <EvidenceRail evidence={[{ source: 'scene', status: 'partial', items: [] }]} />
    )
    expect(unknownStatus.container.querySelector('.evidence-rail')).toBeNull()
  })

  it('文案一律「检索来源」，不出现「依据」「引用」', () => {
    const { container } = render(
      <EvidenceRail evidence={[sceneHit(), { source: 'memory', status: 'hit', items: [{ text: '记得' }] }]} />
    )
    fireEvent.click(container.querySelector('.evidence-toggle'))
    expect(container.textContent).toContain('检索来源')
    expect(container.textContent).not.toContain('依据')
    expect(container.textContent).not.toContain('引用')
  })
})
