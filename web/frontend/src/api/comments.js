import { fetchWithTimeout } from './client'

// 评论端点（三种评论各一套路径）的唯一入口 —— 组件不手写这些 URL。
// fetchWithTimeout 会自己带鉴权头、自己把失败转成带 detail 的 AppError，调用点不用管。

const LIKE_URL = {
  text: (commentId) => `/api/text/comments/${commentId}/like`,
  post: (commentId) => `/api/market/post/comments/${commentId}/like`,
  card: (commentId) => `/api/market/comments/${commentId}/like`,
}

/** 点赞 / 取消点赞一条评论，返回 `{liked, likes}`。 */
export function likeComment(kind, commentId) {
  return fetchWithTimeout(LIKE_URL[kind](commentId), { method: 'POST' }).then((r) => r.json())
}
