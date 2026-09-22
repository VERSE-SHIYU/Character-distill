import { describe, it, expect } from 'vitest'
import { formatEmbeddingTestError } from './formatEmbeddingTestError'

describe('formatEmbeddingTestError', () => {
  it('V1 returns the Chinese hint and the raw text as separate fields', () => {
    expect(formatEmbeddingTestError({
      ok: false,
      error: 'API Key 无效，或与所选地域不匹配（中国站的 Key 选 cn，国际站的 Key 选 intl）',
      detail: 'Error code: 401 - invalid_api_key',
    })).toEqual({
      hint: 'API Key 无效，或与所选地域不匹配（中国站的 Key 选 cn，国际站的 Key 选 intl）',
      detail: 'Error code: 401 - invalid_api_key',
    })
  })

  it('V2 gives an empty detail — not undefined, not a stray newline — when there is none', () => {
    expect(formatEmbeddingTestError({ ok: false, error: '请求过于频繁，请稍后再试' }))
      .toEqual({ hint: '请求过于频繁，请稍后再试', detail: '' })
  })

  it('V3 falls back to 未知错误 when error is missing', () => {
    expect(formatEmbeddingTestError({ ok: false }))
      .toEqual({ hint: '未知错误', detail: '' })
    expect(formatEmbeddingTestError(undefined))
      .toEqual({ hint: '未知错误', detail: '' })
  })

  it('V4 keeps a multi-line detail verbatim', () => {
    const detail = 'Error code: 400 - {\n  "code": "Arrearage",\n  "message": "overdue"\n}'
    expect(formatEmbeddingTestError({ ok: false, error: '阿里云账户欠费，请充值后重试', detail }))
      .toEqual({ hint: '阿里云账户欠费，请充值后重试', detail })
  })
})
