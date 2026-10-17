import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { exportCard, setToken, setRefreshToken } from '../client'

// 导出必须走 fetchWithTimeout：access token 过期（401）时先刷新再重试，
// 其余按钮能静默续期，导出不能例外。裸 fetch 不会刷新，用户只会看到「导出失败」。

const json = (body) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
})

describe('exportCard token 续期', () => {
  let fetchMock
  let downloads

  beforeEach(() => {
    localStorage.clear()
    setToken('old-token')
    setRefreshToken('refresh-1')
    downloads = []
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      downloads.push(this.download)
    })
    URL.createObjectURL = vi.fn(() => 'blob:export')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('401 时先刷新再重试，成功后触发下载', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(json({ access_token: 'new-token', refresh_token: 'refresh-2' }))
      .mockResolvedValueOnce(new Response('{"name":"角色甲"}', {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Content-Disposition': "attachment; filename*=UTF-8''%E8%A7%92%E8%89%B2%E7%94%B2.json",
        },
      }))

    await exportCard('card1')

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/refresh')
    expect(fetchMock.mock.calls[2][0]).toBe('/api/distill/cards/card1/export?format=raw')
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer new-token')
    expect(downloads).toEqual(['角色甲.json'])
  })
})
