export class AppError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'AppError'
    this.status = status
  }
}

export async function fetchWithTimeout(url, opts = {}, ms = 600000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  try {
    const res = await fetch(url, { ...opts, signal: controller.signal })
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const body = await res.json()
        if (body.detail) detail = body.detail
      } catch { /* ignore */ }
      throw new AppError(detail, res.status)
    }
    return res
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

  ;(async () => {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok) {
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
              onError(new AppError(payload.error))
              return
            }
            if (payload.done) {
              onDone(payload)
              return
            }
            if (payload.token !== undefined) {
              onToken(payload.token)
            }
            if (payload.status !== undefined && onStatus) {
              onStatus(payload.status)
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        onError(err instanceof AppError ? err : new AppError(err.message))
      }
    }
  })()

  return () => controller.abort()
}
