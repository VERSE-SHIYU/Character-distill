import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'
import PrivateMessageChat from '../PrivateMessageChat'

// jsdom 缺省能力补齐（useIsMobile / useAutoScroll 依赖）
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
}

const MY = 'u_me'
const PEER = 'u_peer'

// 服务端消息形状（sqlite created_at 文本）
const { serverMsgs, setServerMsgs } = vi.hoisted(() => {
  const arr = []
  return {
    serverMsgs: arr,
    setServerMsgs: (msgs) => { arr.length = 0; arr.push(...msgs) },
  }
})

vi.mock('../../store/useAppStore', () => {
  const state = {
    authUser: { id: 'u_me', username: 'me' },
    userAvatar: null,
    currentCard: null,
    affinity: null,
    fetchAffinity: vi.fn(),
    refreshUnread: vi.fn(),
  }
  const hook = (sel) => sel(state)
  hook.getState = () => state
  hook.setState = (patch) => Object.assign(state, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn((url) => {
    if (url.includes('/messages/with/') && !url.includes('reactions')) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ messages: serverMsgs }) })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
  }),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../components/common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../../components/common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/common/Avatar', () => ({ default: () => null }))
vi.mock('../../components/common/MessageReactions', () => ({ default: () => null }))
vi.mock('../../components/common/ChatInputBar', () => ({ default: () => null }))

// 生成 sqlite 形状消息：now - minsAgo 分钟
const msg = (id, content, minsAgo, over = {}) => {
  const d = new Date(Date.now() - minsAgo * 60000)
  const p = (n) => String(n).padStart(2, '0')
  return {
    id,
    sender_id: MY,
    receiver_id: PEER,
    content,
    is_read: 1,
    created_at: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`,
    cross_border_synced: 1,
    ...over,
  }
}

beforeEach(() => {
  setServerMsgs([])
})

describe('PrivateMessageChat 消息分组（同发送者 <5min 合并）', () => {
  it('连续同一发送者且含乐观无时间戳消息：合并为 1 行，头像可见，N 个气泡', async () => {
    // m2 是发送中的乐观消息，无 created_at —— 不得把整组拆开
    setServerMsgs([
      msg('m1', 'A', 4),
      { id: 'm2', sender_id: MY, content: 'B', _status: 'sending' },
      msg('m3', 'C', 2),
    ])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(1))
    expect(container.querySelectorAll('.dm-avatar').length).toBe(1)
    expect(container.querySelectorAll('.dm-bubble-wrap').length).toBe(3)
  })

  it('跨 5 分钟间隔的消息分为两个 row', async () => {
    setServerMsgs([msg('m1', 'A', 7), msg('m2', 'B', 1)]) // 6min 间隔
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(2))
    expect(container.querySelectorAll('.dm-bubble-wrap').length).toBe(2)
  })

  it('干净的连续消息合并为 1 行，且不添加 grouped class（头像不被 visibility:hidden 藏掉）', async () => {
    setServerMsgs([msg('m1', 'A', 4), msg('m2', 'B', 3), msg('m3', 'C', 2), msg('m4', 'D', 1)])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(1))
    const row = container.querySelector('.dm-row')
    expect(row.classList.contains('grouped')).toBe(false)
    const avatar = container.querySelector('.dm-avatar')
    expect(avatar && getComputedStyle(avatar).visibility).not.toBe('hidden')
    expect(container.querySelectorAll('.dm-bubble-wrap').length).toBe(4)
  })

  it('发送者切换拆行；对方行只显示一次昵称', async () => {
    setServerMsgs([
      msg('m1', 'A', 4),
      { ...msg('m2', 'B', 3), sender_id: PEER },
      msg('m3', 'C', 2),
    ])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(3))
    expect(container.querySelectorAll('.dm-name').length).toBe(1)
    expect(container.querySelector('.dm-name').textContent).toBe('沈星回')
  })
})

describe('PrivateMessageChat 整组全撤回时隐藏头像（设计稿最后一处差异）', () => {
  it('一组内全部消息被撤回：头像带 dm-avatar--hidden，且非 display:none', async () => {
    setServerMsgs([msg('r1', 'A', 4, { retracted: 1 }), msg('r2', 'B', 3, { retracted: 1 })])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(1))
    const avatar = container.querySelector('.dm-avatar')
    expect(avatar).toBeTruthy()
    expect(avatar.classList.contains('dm-avatar--hidden')).toBe(true)
    // 硬性约束锁死：visibility 而非 display（display:none 会让占位宽度消失、气泡右缘错位）
    expect(avatar.style.display).not.toBe('none')
  })

  it('一组内部分撤回（仍有正常消息）：头像正常显示', async () => {
    setServerMsgs([msg('p1', 'A', 4, { retracted: 1 }), msg('p2', 'B', 3)])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(1))
    const avatar = container.querySelector('.dm-avatar')
    expect(avatar.classList.contains('dm-avatar--hidden')).toBe(false)
  })

  it('一组内无撤回消息：头像正常显示', async () => {
    setServerMsgs([msg('n1', 'A', 4), msg('n2', 'B', 3)])
    const { container } = render(<PrivateMessageChat otherUserId={PEER} otherUsername="沈星回" />)
    await waitFor(() => expect(container.querySelectorAll('.dm-row').length).toBe(1))
    const avatar = container.querySelector('.dm-avatar')
    expect(avatar.classList.contains('dm-avatar--hidden')).toBe(false)
  })

  it('.dm-avatar--hidden 在 global.css 定义为 visibility:hidden（保留占位宽度，不用 display）', () => {
    const css = fs.readFileSync(path.join(__dirname, '../../styles/global.css'), 'utf8')
    const block = css.match(/\.dm-avatar--hidden\s*\{([^}]*)\}/)
    expect(block).toBeTruthy()
    expect(block[1]).toMatch(/visibility\s*:\s*hidden/)
    expect(block[1]).not.toMatch(/display/)
  })
})
