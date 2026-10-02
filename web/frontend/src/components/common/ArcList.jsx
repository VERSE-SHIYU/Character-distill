// 角色弧线：一条变化轴 + 按故事顺序排列的阶段。卡片详情两处（自建卡 / 集市卡）共用。
// `arc` 是 parseCardJson 归一后的 { axis, phases: [{ label, state }] }；旧卡的阶段没有 label。
export default function ArcList({ arc }) {
  return (
    <>
      {arc.axis && <p className="card-arc-axis">{arc.axis}</p>}
      <ol className="card-arc-list">
        {arc.phases.map((p, i) => (
          <li key={i} className="card-arc-item">
            <span className="card-arc-index">{i + 1}</span>
            <span className="card-arc-text">
              {p.label && <strong className="card-arc-label">{p.label}</strong>}
              {p.label && p.state && ' · '}
              {p.state}
            </span>
          </li>
        ))}
      </ol>
    </>
  )
}
