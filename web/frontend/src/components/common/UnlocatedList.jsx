// 未定位区：摘录在原文里定不了位的条目（做法、字段值、关系态度）。只展示、不进聊天的 prompt；
// 卡主把一条挪进某个阶段，就算给了依据（spec arc-phase-unlocated §6）。
//
// 规则全在后端（core/unlocated.py：列表追加 / 单值交换 / 态度去占位），这里只渲染、收集
// 「挪到第几阶段」，交给 `onMove(section, index, phase, path)`。阶段选择用原生 <select>
// （键盘、读屏、定位都现成，不重造）。默认选中：态度用它原来标的阶段，其余用最后阶段。
import { useState } from 'react'
import { overlayLeaves, overlayLabel } from './ArcList'

function MoveControl({ phases, initial, label, onMove }) {
  const [phase, setPhase] = useState(initial)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { await onMove(phase) } finally { setBusy(false) }
  }
  return (
    <span className="card-unlocated-move">
      <select
        aria-label={`把「${label}」挪到哪个阶段`}
        value={phase}
        onChange={(e) => setPhase(Number(e.target.value))}
        disabled={busy}
      >
        {phases.map((p, i) => (
          <option key={i} value={i + 1}>{`阶段 ${i + 1}${p.label ? ` · ${p.label}` : ''}`}</option>
        ))}
      </select>
      <button type="button" className="btn-secondary btn-sm" onClick={run} disabled={busy}>
        挪入
      </button>
    </span>
  )
}

export function hasUnlocated(arc) {
  const u = arc?.unlocated
  if (!u) return false
  return (u.behaviors?.length || 0) + (u.attitudes?.length || 0)
    + overlayLeaves(u.overlay).reduce((n, [, v]) => n + (v?.length || 0), 0) > 0
}

export default function UnlocatedList({ arc, onMove }) {
  const phases = arc.phases || []
  const u = arc.unlocated || {}
  const last = phases.length
  if (!last || !hasUnlocated(arc)) return null
  const rows = [
    ...(u.behaviors || []).map((b, i) => ({
      key: `b${i}|${b.situation}|${b.behavior}`, kind: '做法', text: `${b.situation} → ${b.behavior}`,
      move: (p) => onMove('behaviors', i, p, ''), initial: last,
    })),
    ...overlayLeaves(u.overlay).flatMap(([path, vals]) => (vals || []).map((v, i) => ({
      key: `o${path}|${i}|${v}`, kind: overlayLabel(path), text: v,
      move: (p) => onMove('overlay', i, p, path), initial: last,
    }))),
    ...(u.attitudes || []).map((a, i) => ({
      key: `a${i}|${a.target}|${a.attitude}`, kind: `对${a.target}的态度`, text: a.attitude,
      move: (p) => onMove('attitudes', i, p, ''),
      initial: a.phase >= 1 && a.phase <= last ? a.phase : last,
    })),
  ]
  return (
    <div className="card-unlocated">
      <p className="card-unlocated-hint">
        这些条目在原文里找不到可靠的时期，聊天时不会用上；确认属于哪个阶段后可以挪进去。
      </p>
      <ul className="card-unlocated-list">
        {rows.map((r) => (
          <li key={r.key} className="card-unlocated-item">
            <span className="card-unlocated-kind pill">{r.kind}</span>
            <span className="card-unlocated-text">{r.text}</span>
            <MoveControl phases={phases} initial={r.initial} label={r.text} onMove={r.move} />
          </li>
        ))}
      </ul>
    </div>
  )
}
