import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import fs from 'node:fs'
import path from 'node:path'
import UnsavedHint from '../common/UnsavedHint'
import ChatArea from '../ChatArea'

// 94 口径统一：保存失败不中断本轮，失败的那一条自己标「未保存，刷新后会丢失」。
// 文案只有这一份 —— 四个落点各写各的，早晚出现三种说法，而用户看到的是同一件事。

// jsdom 缺省能力补齐（同 ChatAreaTopbar.test.jsx）
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

const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    currentCard: { id: 'c1', name: '测试角色', text_id: 't1' },
    sessionId: 's1',
    currentView: 'chat',
    resumeLoading: false,
    chatSnapshot: null,
    archiveModalOpen: false,
    _pendingChatCardId: null,
    messages: [],
    sending: false,
    userRolesByCard: {},
    sessionUserRole: '',
    currentTextId: 't1',
    texts: [],
    voiceStatus: null,
    isRecording: false,
    recordingDuration: 0,
    revokeCooldown: 0,
    webSearchEnabled: false,
    agentMode: false,
    affinity: null,
    affinityEnabled: true,
    authUser: { id: 'u1' },
    cardAvatars: { c1: 'data:image/png;base64,test' },
    userAvatar: null,
    currentSessionAvatar: null,
    voiceList: [],
    viewHistory: [],
    loadVoices: () => {},
    clearMessageTyping: () => {},
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})
vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../common/ChatInputBar', () => ({ default: () => null }))
vi.mock('../common/ChatSessionList', () => ({ default: () => null }))
vi.mock('../common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../common/Avatar', () => ({ default: () => null }))
vi.mock('../common/Loading', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../common/MessageReactions', () => ({ default: () => null }))
vi.mock('../common/ReplyQuote', () => ({ default: () => null }))
vi.mock('../PageHeader', () => ({ default: () => null }))
// ChatBubble 不 mock —— 提示就渲染在它里面，mock 掉等于把被测的东西一起去掉了。

describe('UnsavedHint', () => {
  it('V3 文案只有这一句，且用自己那个类名（不借 danger 红，那是「发送失败可重试」的）', () => {
    const { container } = render(<UnsavedHint />)

    const hint = container.querySelector('.unsaved-hint')
    expect(hint).not.toBeNull()
    expect(hint.textContent).toBe('未保存，刷新后会丢失')
  })

  it('V3 样式表里 .unsaved-hint 是次要色小号字，且不是 display:none', () => {
    const css = fs.readFileSync(path.join(__dirname, '../../styles/global.css'), 'utf8')
    const block = css.match(/\.unsaved-hint\s*\{([^}]*)\}/)
    expect(block).toBeTruthy()
    expect(block[1]).toMatch(/font-size\s*:\s*11px/)
    expect(block[1]).toMatch(/color\s*:\s*var\(--text-secondary\)/)
    expect(block[1]).not.toMatch(/display\s*:\s*none/)
  })

  it('V3 一对一气泡：unsaved 的那条渲染提示，普通那条不渲染', () => {
    mutate({
      messages: [
        { _cid: 'm1', role: 'user', content: '没存上的那条', unsaved: true },
        { _cid: 'm2', role: 'user', content: '存上了的那条' },
      ],
    })
    const { container } = render(<ChatArea />)

    const hints = container.querySelectorAll('.unsaved-hint')
    expect(hints.length).toBe(1)
    expect(hints[0].textContent).toBe('未保存，刷新后会丢失')
    expect(hints[0].closest('.chat-msg').textContent).toContain('没存上的那条')
  })
})
