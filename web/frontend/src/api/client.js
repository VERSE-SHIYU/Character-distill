export class AppError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'AppError'
    this.status = status
  }
}

// ---- Auth helpers ----

const TOKEN_KEY = 'auth_token'
const REFRESH_KEY = 'refresh_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function removeToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY) || ''
}

export function setRefreshToken(token) {
  if (token) localStorage.setItem(REFRESH_KEY, token)
  else localStorage.removeItem(REFRESH_KEY)
}

export function removeAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export function getAuthHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

let _refreshPromise = null

async function tryRefresh() {
  const rt = getRefreshToken()
  if (!rt) return null
  if (_refreshPromise) return _refreshPromise
  _refreshPromise = (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      })
      if (!res.ok) throw new Error('refresh failed')
      const data = await res.json()
      setToken(data.access_token)
      setRefreshToken(data.refresh_token)
      return data.access_token
    } catch {
      removeAuth()
      return null
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}

export async function fetchWithTimeout(url, opts = {}, ms = 600000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)

  const doFetch = async (headers) => {
    const res = await fetch(url, { ...opts, headers, signal: controller.signal })
    if (res.status === 429) {
      let detail = '请求过于频繁，请稍后再试'
      try { const b = await res.json(); if (b.detail) detail = b.detail } catch {}
      throw new AppError(detail, 429)
    }
    if (res.status === 401 && !url.endsWith('/api/auth/refresh') && !url.endsWith('/api/auth/login') && !url.endsWith('/api/auth/register')) {
      // Try refresh once, then retry original request
      const newToken = await tryRefresh()
      if (newToken) {
        const newHeaders = { ...headers, Authorization: `Bearer ${newToken}` }
        const retryRes = await fetch(url, { ...opts, headers: newHeaders, signal: controller.signal })
        if (!retryRes.ok) {
          let detail = `HTTP ${retryRes.status}`
          try { const b = await retryRes.json(); if (b.detail) detail = b.detail } catch {}
          throw new AppError(detail, retryRes.status)
        }
        return retryRes
      }
      removeAuth()
      throw new AppError('请重新登录', 401)
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const body = await res.json()
        if (body.detail) detail = body.detail
      } catch { /* ignore */ }
      throw new AppError(detail, res.status)
    }
    return res
  }

  try {
    const headers = { ...getAuthHeaders(), ...(opts.headers || {}) }
    return await doFetch(headers)
  } catch (err) {
    if (err instanceof AppError) throw err
    if (err.name === 'AbortError') throw new AppError('Request timed out', 408)
    throw new AppError(err.message || 'Network error', 0)
  } finally {
    clearTimeout(timer)
  }
}

export async function postJSON(url, body, ms) {
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, ms)
  return res.json()
}

export function streamSSE(url, body, onToken, onDone, onError, onStatus) {
  const controller = new AbortController()
  const timeoutMs = 600000 // 10 min total
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  let finished = false

  ;(async () => {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok) {
        if (res.status === 401) {
          const newToken = await tryRefresh()
          if (newToken) {
            onError(new AppError('Token 已刷新，请重试', 401))
          } else {
            removeAuth()
            onError(new AppError('请重新登录', 401))
          }
          return
        }
        if (res.status === 429) {
          onError(new AppError('请求过于频繁，请稍后再试', 429))
          return
        }
        let detail = `HTTP ${res.status}`
        try {
          const b = await res.json()
          if (b.detail) detail = b.detail
        } catch { /* ignore */ }
        onError(new AppError(detail, res.status))
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const payload = JSON.parse(line.slice(6))
            if (payload.error) {
              finished = true
              onError(new AppError(payload.error))
              return
            }
            if (payload.done) {
              finished = true
              onDone(payload)
              return
            }
            if (payload.token !== undefined) {
              onToken(payload.token)
            }
            if (payload.status !== undefined && onStatus) {
              onStatus(payload)
            }
          } catch { /* skip malformed lines */ }
        }
      }

      // Stream ended without done/error event — connection lost or LLM truncated
      if (!finished) {
        onError(new AppError('连接中断，请重试'))
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        onError(err instanceof AppError ? err : new AppError(err.message))
      } else if (!finished) {
        onError(new AppError('请求超时，请重试', 408))
      }
    } finally {
      clearTimeout(timeoutId)
    }
  })()

  return () => controller.abort()
}

// ---- Admin API ----

export const adminAPI = {
  listUsers: () => postJSON('/api/admin/users', {}),

  disableUser: (userId) => postJSON(`/api/admin/users/${userId}/disable`, {}),

  enableUser: (userId) => postJSON(`/api/admin/users/${userId}/enable`, {}),

  resetPassword: (userId, newPassword) => fetchWithTimeout(
    `/api/admin/users/${userId}/reset-password`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_password: newPassword }),
    },
  ),

  generateInvites: (count = 1) => postJSON('/api/admin/invite/generate', { count }),

  listInvites: () => postJSON('/api/admin/invite/list', {}),
}
