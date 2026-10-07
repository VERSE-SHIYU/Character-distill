// 角色弧线：一条变化轴 + 按故事顺序排列的阶段。卡片详情两处（自建卡 / 集市卡）共用。
// `arc` 是 parseCardJson 归一后的 { axis, phases: [{ label, state, behaviors, overlay }] }；旧卡的阶段没有 label。
// 阶段下的 behaviors 是只在那个阶段成立的做法，跟在阶段后面显示。
// `overlay` 是「只在这个阶段成立」的字段，与卡片同形（`{ speaking_style: { catchphrases: [...] } }`）；
// 逐层走到叶子、把键拼回登记表路径（`speaking_style.catchphrases`）再查标签。认不出的路径显示
// 原名 —— 后端加了新路径而这里没跟上时，内容照样列出来，不静默丢。
import BehaviorList from './BehaviorList'

const OVERLAY_LABELS = {
  personality_traits: '性格',
  values: '价值观',
  inner_tensions: '内心矛盾',
  emotional_patterns: '情绪模式',
  'speaking_style.tone': '语气',
  'speaking_style.sentence_pattern': '句式',
  'speaking_style.catchphrases': '口头禅',
  'psyche.triggers': '雷点',
  'psyche.soft_spots': '软肋',
  decision_style: '决策风格',
  'cognitive.speech_style': '说话风格',
  'cognitive.knowledge_scope': '见识范围',
  key_memories: '记忆',
  dialogue_examples: '对白示例',
}

// 与卡片同形的 overlay → [[登记表路径, 值]]：遇到普通对象往下走，其余都是叶子。
export function overlayLeaves(overlay, prefix = '') {
  return Object.entries(overlay || {}).flatMap(([k, v]) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? overlayLeaves(v, `${prefix}${k}.`)
      : [[`${prefix}${k}`, v]])
}

export function overlayLabel(path) {
  return OVERLAY_LABELS[path] || path
}

function OverlayBlock({ overlay }) {
  const entries = overlayLeaves(overlay)
    .filter(([, v]) => (Array.isArray(v) ? v.length > 0 : Boolean(v)))
  if (entries.length === 0) return null
  return (
    <dl className="card-arc-overlay">
      {entries.map(([key, value]) => (
        <div key={key} className="card-arc-overlay-field">
          <dt className="card-arc-overlay-label">{overlayLabel(key)}</dt>
          <dd className="card-arc-overlay-value">
            {Array.isArray(value)
              ? <ul className="card-arc-overlay-list">
                  {value.map((v, j) => <li key={j}>{v}</li>)}
                </ul>
              : value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export default function ArcList({ arc }) {
  return (
    <>
      {arc.axis && <p className="card-arc-axis">{arc.axis}</p>}
      <ol className="card-arc-list">
        {arc.phases.map((p, i) => (
          <li key={i} className="card-arc-item">
            <span className="card-arc-index">{i + 1}</span>
            <div className="card-arc-body">
              <span className="card-arc-text">
                {p.label && <strong className="card-arc-label">{p.label}</strong>}
                {p.label && p.state && ' · '}
                {p.state}
              </span>
              {p.behaviors?.length > 0 && <BehaviorList items={p.behaviors} />}
              <OverlayBlock overlay={p.overlay} />
              {p.memories?.length > 0 && (
                <ul className="card-arc-memories">
                  {p.memories.map((m, j) => <li key={j} className="card-arc-memory">{m}</li>)}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
    </>
  )
}
