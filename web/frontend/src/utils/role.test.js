import { describe, it, expect } from 'vitest'
import { isAdmin, isGuest } from './role.js'

// 四种输入各一条：guest / user / admin / null。
// 判据是「对 guest 为 true，其余三种一律 false」——null 那条尤其重要：
// 未登录时 authUser 为 null，若 isGuest 在这里返回 true，会把整个 app 当成游客藏掉。
describe('isGuest', () => {
  it('guest → true', () => {
    expect(isGuest({ role: 'guest' })).toBe(true)
  })

  it('user → false', () => {
    expect(isGuest({ role: 'user' })).toBe(false)
  })

  it('admin → false', () => {
    expect(isGuest({ role: 'admin' })).toBe(false)
  })

  it('null / undefined → false', () => {
    expect(isGuest(null)).toBe(false)
    expect(isGuest(undefined)).toBe(false)
  })

  it('无 role 字段 → false', () => {
    expect(isGuest({})).toBe(false)
  })
})

describe('isAdmin', () => {
  it('admin → true，其余 → false', () => {
    expect(isAdmin({ role: 'admin' })).toBe(true)
    expect(isAdmin({ role: 'user' })).toBe(false)
    expect(isAdmin({ role: 'guest' })).toBe(false)
    expect(isAdmin(null)).toBe(false)
  })
})
