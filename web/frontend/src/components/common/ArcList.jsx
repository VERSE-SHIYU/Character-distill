// 角色弧线：一条变化轴 + 按故事顺序排列的阶段。卡片详情两处（自建卡 / 集市卡）共用。
// `arc` 是 parseCardJson 归一后的 { axis, phases: [{ label, state, behaviors }] }；旧卡的阶段没有 label。
// 阶段下的 behaviors 是只在那个阶段成立的做法，跟在阶段后面显示。
import BehaviorList from './BehaviorList'

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
