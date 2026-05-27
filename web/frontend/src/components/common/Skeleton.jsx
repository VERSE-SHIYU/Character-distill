export function SkeletonCard({ style }) {
  return (
    <div className="skeleton-card" style={style}>
      <div className="skeleton-card-block skeleton-shimmer" />
      <div className="skeleton-card-line skeleton-card-line-wide skeleton-shimmer" />
      <div className="skeleton-card-line skeleton-shimmer" />
    </div>
  )
}

export function SkeletonRow({ style }) {
  return (
    <div className="skeleton-row" style={style}>
      <div className="skeleton-row-circle skeleton-shimmer" />
      <div className="skeleton-row-lines">
        <div className="skeleton-row-line skeleton-row-line-short skeleton-shimmer" />
        <div className="skeleton-row-line skeleton-shimmer" />
      </div>
    </div>
  )
}
