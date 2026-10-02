import { Close } from './Icon'
import { blankRow } from '../../utils/objectRows'

// 对象列表编辑：一行一个对象，每列一个输入框，可增删行。
// 人物关系 / 弧线阶段 / 情境→行为共用这一份 —— 三者只是列（columns）不同。
// maxCount 只管「还能不能再加」；已有的行超出上限时照常显示。
export default function ObjectListField({ legend, rows, onChange, columns, maxCount, addLabel }) {
  const update = (idx, key, value) => onChange(rows.map((r, i) => (i === idx ? { ...r, [key]: value } : r)))

  return (
    <fieldset className="edit-fieldset">
      <legend className="modal-label">{legend}</legend>
      {rows.map((r, i) => (
        <div key={r._key} className="edit-rel-row">
          {columns.map((c) => (
            <input
              key={c.key}
              className="modal-input edit-rel-input"
              style={c.flex ? { flex: c.flex } : undefined}
              placeholder={c.placeholder}
              value={r[c.key] ?? ''}
              onChange={(e) => update(i, c.key, e.target.value)}
              maxLength={c.maxLength}
            />
          ))}
          <button type="button" className="btn-secondary edit-rel-del" aria-label="删除这一行" onClick={() => onChange(rows.filter((_, j) => j !== i))}><Close size={12} /></button>
        </div>
      ))}
      <button type="button" className="btn-secondary mt-6" onClick={() => onChange([...rows, blankRow(columns)])} disabled={rows.length >= maxCount}>{addLabel}</button>
    </fieldset>
  )
}
