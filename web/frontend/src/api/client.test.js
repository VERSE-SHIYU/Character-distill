import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { getAuthHeaders, clientTz } from './client'

const TOKEN_KEY = 'auth_token'

// 后端在请求入口按 `Time-Zone` 头 → 用户已存时区 → 默认 的顺序定时区（缺陷 96）。
// 这个头必须由 `getAuthHeaders()` 一处发出 —— 它是全仓唯一发往本后端的请求头出处。
describe('getAuthHeaders 发 Time-Zone', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('有 token：Authorization 与 Time-Zone 都在', () => {
    localStorage.setItem(TOKEN_KEY, 'tok-1')
    const h = getAuthHeaders()
    expect(h.Authorization).toBe('Bearer tok-1')
    expect(h['Time-Zone']).toBe(clientTz())
  })

  // 反过来写会漏掉匿名/公开路径：头一旦放进 token 三元里，没登录的请求就不带了。
  it('没有 token：仍带 Time-Zone', () => {
    const h = getAuthHeaders()
    expect(h.Authorization).toBeUndefined()
    expect(h['Time-Zone']).toBe(clientTz())
  })

  it('取不到时区名时不发这个头（也不发空串）', () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
      throw new Error('no Intl here')
    })
    const h = getAuthHeaders()
    expect('Time-Zone' in h).toBe(false)
  })

  it('clientTz 取不到时返回空串而不是抛', () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
      throw new Error('no Intl here')
    })
    expect(clientTz()).toBe('')
  })
})
