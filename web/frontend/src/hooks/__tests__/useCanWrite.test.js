import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import useCanWrite from '../useCanWrite'

// store 用可变对象注入：换 authUser 就是换身份。
const { mockState, mutate } = vi.hoisted(() => {
  const state = { authUser: null }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})

beforeEach(() => mutate({ authUser: null }))

describe('useCanWrite', () => {
  it('guest → false', () => {
    mutate({ authUser: { role: 'guest' } })
    expect(renderHook(() => useCanWrite()).result.current).toBe(false)
  })

  it('user → true', () => {
    mutate({ authUser: { role: 'user' } })
    expect(renderHook(() => useCanWrite()).result.current).toBe(true)
  })

  it('admin → true', () => {
    mutate({ authUser: { role: 'admin' } })
    expect(renderHook(() => useCanWrite()).result.current).toBe(true)
  })

  it('未登录（null）→ true —— 锁屏页不是游客，不能把入口藏掉', () => {
    mutate({ authUser: null })
    expect(renderHook(() => useCanWrite()).result.current).toBe(true)
  })
})
