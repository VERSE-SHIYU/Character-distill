import { Heart } from './Icon'

/**
 * 三种评论（文本 / 帖子 / 卡片）共用的点赞按钮。
 *
 * 纯展示：状态和请求都在调用方，这里只画 `liked` / `count` 并把点击交回去。
 *
 * `canWrite` 为假（游客）时只显示计数、不显示按钮。这条分支实际只作用于帖子和卡片评论：
 * 文本评论那一行整行在调用方的 `canWrite` 判断之内，游客连计数也看不到，走的不是这里。
 */
export default function CommentLikeButton({ liked, count, onClick, canWrite = true }) {
  const label = (
    <>
      {liked ? <Heart size={12} fill="currentColor" /> : <Heart size={12} />} {count || 0}
    </>
  )

  if (!canWrite) {
    return <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{label}</span>
  }

  return (
    <button
      type="button"
      className="btn-ghost"
      style={{
        padding: '2px 6px',
        fontSize: 12,
        color: liked ? 'var(--danger)' : 'var(--text-dim)',
      }}
      onClick={onClick}
    >
      {label}
    </button>
  )
}
