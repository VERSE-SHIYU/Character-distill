// 情境→行为：此人在某类情境下的具体做法。卡片详情的「情境→行为」一节（从头到尾都成立的）
// 与 ArcList 的每个阶段（只在那个阶段成立的）共用。
// 原文摘录经落卡前核对，查不到的已被清空 —— 有就显示，没有就不占位。
export default function BehaviorList({ items }) {
  return (
    <ul className="card-behavior-list">
      {items.map((b, i) => (
        <li key={i} className="card-behavior-item">
          <span className="card-behavior-situation pill">{b.situation}</span>
          <span className="card-behavior-text">{b.behavior}</span>
          {b.source_quote && <q className="card-behavior-quote">{b.source_quote}</q>}
        </li>
      ))}
    </ul>
  )
}
