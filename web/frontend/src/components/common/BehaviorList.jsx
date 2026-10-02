// 情境→行为：此人在某类情境下的具体做法（各阶段都成立）。卡片详情两处共用。
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
