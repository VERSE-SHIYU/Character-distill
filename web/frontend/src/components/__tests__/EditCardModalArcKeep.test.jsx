/**
 * spec arc-phase-unlocated §4 段 2：编辑保存只替换表单编辑的两项，起点指纹与未定位区原样带回。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EditCardModal from '../EditCardModal'

describe('EditCardModal 保存不丢阶段信息', () => {
  it('起点指纹与未定位区原样带回（改前整体替换 character_arc，指纹丢失）', async () => {
    const onSave = vi.fn().mockResolvedValue()
    const unlocated = { behaviors: [{ situation: 's', behavior: 'b' }], overlay: {}, attitudes: [] }
    const data = { name: 'x', character_arc: { axis: 'a', source_fingerprint: 'FP', unlocated,
      phases: [{ label: 'l', state: 's', start: 0 }] } }
    render(<EditCardModal isOpen data={data} cardId="c" onSave={onSave} onClose={() => {}} />)
    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    const arc = onSave.mock.calls[0][0].character_arc
    expect([arc.source_fingerprint, arc.unlocated, arc.axis]).toEqual(['FP', unlocated, 'a'])
  })
})
