import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./client', () => ({ fetchWithTimeout: vi.fn() }))

import { fetchWithTimeout } from './client'
import { likeComment } from './comments'

// 三种评论各一套路径，这里是唯一的出处 —— 组件不手写这些 URL。
// 路径写错不会在别处报错，只会静默 404 / 打到别的资源上，所以要钉住。
describe('likeComment 的 URL 映射', () => {
  beforeEach(() => {
    fetchWithTimeout.mockReset()
    fetchWithTimeout.mockResolvedValue({ json: async () => ({ liked: true, likes: 3 }) })
  })

  it.each([
    ['text', 'c1', '/api/text/comments/c1/like'],
    ['post', 'c2', '/api/market/post/comments/c2/like'],
    ['card', 'c3', '/api/market/comments/c3/like'],
  ])('%s / %s → %s', async (kind, id, url) => {
    await likeComment(kind, id)
    // 选项对象是全等的：钉住「只带 method」——鉴权头由 fetchWithTimeout 自己加。
    expect(fetchWithTimeout).toHaveBeenCalledWith(url, { method: 'POST' })
  })

  it('把响应解析成 {liked, likes}', async () => {
    expect(await likeComment('post', 'c2')).toEqual({ liked: true, likes: 3 })
  })
})
