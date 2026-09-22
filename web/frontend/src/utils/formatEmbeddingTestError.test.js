import { describe, it, expect } from 'vitest'
import { formatEmbeddingTestError } from './formatEmbeddingTestError'

describe('formatEmbeddingTestError', () => {
  it('V1 puts the Chinese hint first and the raw text after a newline', () => {
    expect(formatEmbeddingTestError({
      ok: false,
      error: 'API Key 无效，或与所选地域不匹配（中国站的 Key 选 cn，国际站的 Key 选 intl）',
      detail: 'Error code: 401 - invalid_api_key',
    })).toBe(
      'API Key 无效，或与所选地域不匹配（中国站的 Key 选 cn，国际站的 Key 选 intl）'
      + '\n详细信息：Error code: 401 - invalid_api_key',
    )
  })

  it('V2 has no trailing blank line or "undefined" when detail is absent', () => {
    const out = formatEmbeddingTestError({ ok: false, error: '请求过于频繁，请稍后再试' })
    expect(out).toBe('请求过于频繁，请稍后再试')
    expect(out).not.toContain('\n')
    expect(out).not.toContain('undefined')
  })

  it('V3 falls back to 未知错误 when error is missing', () => {
    expect(formatEmbeddingTestError({ ok: false })).toBe('未知错误')
    expect(formatEmbeddingTestError(undefined)).toBe('未知错误')
  })
})
