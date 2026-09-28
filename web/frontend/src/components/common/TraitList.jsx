// 性格特征列表。每条是一整句话，不是短标签：高度由 padding + line-height 撑开，
// 换行即变高，所以样式里没有固定高度、也不是胶囊圆角（见 global.css 的 .trait-item）。
export default function TraitList({ items }) {
  return (
    <ul className="trait-list">
      {items.map((t, i) => <li key={i} className="trait-item">{t}</li>)}
    </ul>
  )
}
