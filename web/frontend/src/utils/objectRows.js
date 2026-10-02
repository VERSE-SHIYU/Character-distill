// 对象列表（人物关系 / 弧线阶段 / 情境→行为）在编辑表单里的行操作。纯函数，无 React。
//
// 行上的 `_key` 只给 React 当 key：进表单时 withRowKeys 加上，保存前 cleanRows 去掉。
// 行里不在 columns 中的键（如关系的 note、行为的 source_quote）原样带着走。

let rowSeq = 0

export function withRowKeys(rows) {
  return (rows || []).map((r) => ({ ...r, _key: ++rowSeq }))
}

export function blankRow(columns) {
  return { ...Object.fromEntries(columns.map((c) => [c.key, ''])), _key: ++rowSeq }
}

// 去掉 `_key`，并丢弃各列全空的行（点了「添加」却没填）。
export function cleanRows(rows, columns) {
  return rows
    .filter((r) => columns.some((c) => String(r[c.key] ?? '').trim()))
    .map((r) => Object.fromEntries(Object.entries(r).filter(([k]) => k !== '_key')))
}
