import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import SettingsPanel from '../SettingsPanel'

// 以 authUser 注入身份：guest 与 user 各渲染一次，断言写类条目的有无。
const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    authUser: null,
    popView: () => {},
    pushView: () => {},
    logout: () => {},
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../PageHeader', () => ({ default: () => null }))
vi.mock('../common/ThemeDrawer', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

const labels = (container) =>
  [...container.querySelectorAll('.entry-list-label')].map((el) => el.textContent)

// 三条写类条目：个人资料 / API 配置 / 语音。
const WRITE_ENTRIES = ['个人资料', 'API 配置', '语音']
// 读类或纯本地：主题 / 法律条款，以及列表外的「退出登录」（登出在白名单里）。
const KEEP_ENTRIES = ['主题', '法律条款', '退出登录']

beforeEach(() => mutate({ authUser: null }))

describe('SettingsPanel 入口按身份过滤', () => {
  it('游客：写类条目一条都不出现', () => {
    mutate({ authUser: { role: 'guest' } })
    const { container } = render(<SettingsPanel />)
    const shown = labels(container)
    for (const l of WRITE_ENTRIES) expect(shown).not.toContain(l)
  })

  it('游客：主题/法律条款/退出登录仍在（登出是白名单动作，不许误藏）', () => {
    mutate({ authUser: { role: 'guest' } })
    const { container } = render(<SettingsPanel />)
    const shown = labels(container)
    for (const l of KEEP_ENTRIES) expect(shown).toContain(l)
  })

  it('普通用户：写类条目全部可见', () => {
    mutate({ authUser: { role: 'user' } })
    const { container } = render(<SettingsPanel />)
    const shown = labels(container)
    for (const l of WRITE_ENTRIES) expect(shown).toContain(l)
  })

  it('管理员：写类条目全部可见', () => {
    mutate({ authUser: { role: 'admin' } })
    const { container } = render(<SettingsPanel />)
    const shown = labels(container)
    for (const l of WRITE_ENTRIES) expect(shown).toContain(l)
  })

  it('管理面板只对管理员可见（既有行为不变）', () => {
    mutate({ authUser: { role: 'user' } })
    expect(labels(render(<SettingsPanel />).container)).not.toContain('管理面板')
    mutate({ authUser: { role: 'admin' } })
    expect(labels(render(<SettingsPanel />).container)).toContain('管理面板')
  })
})
