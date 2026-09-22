import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor, act } from '@testing-library/react'
import Sidebar from '../Sidebar'
import ChatArea from '../ChatArea'
import CharCard from '../CharCard'
import TextPanel from '../TextPanel'
import DistillTaskBar from '../DistillTaskBar'
import MarketPage from '../MarketPage'
import MarketCardDetail from '../MarketCardDetail'
import PostCard from '../common/PostCard'
import MinePage from '../MinePage'
import { fetchWithTimeout } from '../../api/client'

// S0 清单里每个「隐藏落点」一条 it：guest 看不到，user 看得到。
//
// 控件归谁渲染就在谁身上测 —— 不穿过 ChatBubble / PostCard 这类子组件
// 到祖先页面上找。Avatar / ChatBubble / MessageReactions 用真组件渲染，
// 否则断言不到「这个点击入口在不在」。

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    // 宽度感知：useIsMobile 依赖它，测试里改 window.innerWidth 就能切布局
    window.matchMedia = (query) => {
      const m = /max-width:\s*(\d+)px/.exec(query)
      return {
        matches: m ? window.innerWidth <= Number(m[1]) : false, media: query,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, onchange: null,
        dispatchEvent: () => false,
      }
    }
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class {
      observe() {} unobserve() {} disconnect() {}
    }
  }
}

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })),
  getAuthHeaders: vi.fn(() => ({})),
  exportCard: vi.fn(),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))

const { mockState, mutate } = vi.hoisted(() => {
  const state = {
    authUser: null,
    authorUserId: null,
    currentView: 'home',
    unreadTotal: 0,
    userAvatar: null,
    userBanner: null,
    currentCard: null,
    sessionId: null,
    fetchUserBanner: () => Promise.resolve(null),
    setAuthorUserId: () => {},
    popView: () => {},
    pushView: () => {},
    setView: () => {},
    navigateTo: () => {},
    startChat: () => {},
    logout: () => {},
    setMessageTargetUserId: () => {},
    setMessageTargetUsername: () => {},
    uploadUserBanner: () => {},
    marketPostsByUser: {},
    // ChatArea
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
    affinityEnabled: false,
    cardAvatars: {},
    currentSessionAvatar: null,
    voiceList: [],
    viewHistory: [],
    selectText: () => {},
    loadVoices: () => {},
    clearMessageTyping: () => {},
    // TextPanel / 创作页
    loading: false,
    error: null,
    uploadProgress: null,
    uploadTaskProgress: null,
    loadTexts: () => {},
    uploadText: () => {},
    deleteText: () => {},
    setCurrentTextDetailId: () => {},
    setCurrentMarketCardId: () => {},
    loadCards: () => {},
    distillTasks: [],
    distillCharacter: () => {},
    removeDistillTask: () => {},
    // 角色页
    cards: [],
    identifiedChars: [],
    identifying: false,
    distilling: false,
    distillTokenCount: 0,
    distillStatus: '',
    identifyCharacters: () => {},
    setCardAvatar: () => {},
    standaloneCards: [],
    loadStandaloneCards: () => {},
    lastDistilledCardId: null,
    setLastDistilledCardId: () => {},
    viewCard: () => {},
    setError: () => {},
    updateCard: () => {},
    setUserRole: () => {},
    getUserRole: () => '',
    navigateBack: () => {},
    restoreChatSnapshot: () => false,
    // 市场 / 我的
    currentMarketCardId: null,
    currentTextDetailId: null,
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch) }
})

// 只有 store hook 被替换；isTerminal / taskActions 这类纯函数导出保持真身，
// 免得测试里另写一份"我以为"的判定。
vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

vi.mock('../common/GlobalSearchBox', () => ({ default: () => null }))
vi.mock('../common/SplitOrFullscreen', () => ({ default: ({ main }) => main || null }))
vi.mock('../common/ChatSessionList', () => ({ default: () => null }))
vi.mock('../common/ChatHistoryPanel', () => ({ default: () => null }))
vi.mock('../common/ChatInputBar', () => ({ default: () => null }))
vi.mock('../common/Loading', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../RoleSetupModal', () => ({ default: () => null }))

const labels = (container, sel) => [...container.querySelectorAll(sel)].map((el) => el.textContent.trim())

const renderAs = (role, node) => {
  mutate({ authUser: role === null ? null : { id: 'u1', role } })
  return render(node)
}

const CHAT_CARD = { id: 'c1', name: '测试角色', text_id: 't1' }

beforeEach(() => {
  mutate({
    authUser: null,
    currentView: 'home',
    currentCard: CHAT_CARD,
    sessionId: 's1',
    messages: [],
    affinity: null,
    affinityEnabled: false,
  })
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchWithTimeout).mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) })
})

describe('顶层导航：游客看不到「回收站」「群聊」', () => {
  const navLabels = (c) => labels(c, '.sidebar-item-label')

  it('guest：两个 nav item 都不在，其余导航仍在', () => {
    const shown = navLabels(renderAs('guest', <Sidebar open pinned />).container)
    expect(shown).not.toContain('回收站')
    expect(shown).not.toContain('群聊')
    for (const l of ['首页', '创作', '历史', '市场', '动态', '我的']) expect(shown).toContain(l)
  })

  it('user：两个 nav item 都在', () => {
    const shown = navLabels(renderAs('user', <Sidebar open pinned />).container)
    expect(shown).toContain('回收站')
    expect(shown).toContain('群聊')
  })
})

describe('聊天页：游客看不到换头像 / 撤回 / 表情回应 / 角色记忆 / 重置对话', () => {
  const openMoreMenu = (container) => {
    fireEvent.click(container.querySelector('[data-more-trigger]'))
    return labels(container, '.chat-more-item span')
  }

  it('角色头像：guest 不是可点的 button，user 是', () => {
    expect(renderAs('guest', <ChatArea />).container.querySelector('button.dm-peer-avatar')).toBeNull()
    expect(renderAs('user', <ChatArea />).container.querySelector('button.dm-peer-avatar')).toBeInTheDocument()
  })

  it('更多菜单：guest 没有「角色记忆」「重置对话」，user 两个都在', () => {
    const guest = openMoreMenu(renderAs('guest', <ChatArea />).container)
    expect(guest).not.toContain('角色记忆')
    expect(guest).not.toContain('重置对话')

    const user = openMoreMenu(renderAs('user', <ChatArea />).container)
    expect(user).toContain('角色记忆')
    expect(user).toContain('重置对话')
  })

  const withUserMessage = (role) => {
    mutate({ messages: [{ id: 'm1', role: 'user', content: '你好', timestamp: '2026-01-01T00:00:00Z' }] })
    return renderAs(role, <ChatArea />).container
  }

  it('撤回：guest 没有 .chat-revoke-btn，user 有', () => {
    expect(withUserMessage('guest').querySelector('.chat-revoke-btn')).toBeNull()
    expect(withUserMessage('user').querySelector('.chat-revoke-btn')).toBeInTheDocument()
  })

  it('气泡头像：guest 不可点（无 pointer），user 可点', () => {
    expect(withUserMessage('guest').querySelector('.chat-user-avatar').style.cursor).toBe('')
    expect(withUserMessage('user').querySelector('.chat-user-avatar').style.cursor).toBe('pointer')
  })

  it('表情回应：guest 没有快捷回应按钮，仅剩引用回复；user 有 6 个', () => {
    const guest = withUserMessage('guest')
    expect(guest.querySelectorAll('.msg-quick-reaction-btn').length).toBe(0)
    expect(guest.querySelector('.msg-action-btn')).toBeInTheDocument()

    expect(withUserMessage('user').querySelectorAll('.msg-quick-reaction-btn').length).toBe(6)
  })
})

describe('创作页：游客没有上传区、卡片「编辑」「删除」', () => {
  const TEXT = { id: 't1', title: '文本1', filename: 'a.txt' }
  const CARD = { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1' }

  const renderCardMenu = async (role) => {
    mutate({ texts: [TEXT] })
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      const body = url.startsWith('/api/distill/cards/by-text/') ? [CARD] : []
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    })
    const { container } = renderAs(role, <TextPanel />)
    fireEvent.click([...container.querySelectorAll('.creation-tab')].find((b) => b.textContent === '角色管理'))
    await waitFor(() => expect(container.querySelector('.creation-char-menu-btn')).toBeInTheDocument())
    fireEvent.click(container.querySelector('.creation-char-menu-btn'))
    return labels(container, '.creation-char-dropdown button')
  }

  it('上传区：guest 不在，user 在', () => {
    mutate({ texts: [] })
    expect(renderAs('guest', <TextPanel />).container.querySelector('.text-upload-zone')).toBeNull()
    expect(renderAs('user', <TextPanel />).container.querySelector('.text-upload-zone')).toBeInTheDocument()
  })

  it('角色卡菜单：guest 只剩「聊天」「发布到市场」，user 四个都在', async () => {
    const guest = await renderCardMenu('guest')
    expect(guest).toEqual(['聊天', '发布到市场'])

    const user = await renderCardMenu('user')
    expect(user).toEqual(['编辑', '聊天', '发布到市场', '删除'])
  })
})

describe('角色页：游客看不到识别/蒸馏/编辑/分享/上传头像，回收站操作也不可见', () => {
  const CARD = { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲","identity":"学生"}', text_id: 't1' }

  const renderCharCard = (role) => {
    mutate({
      currentTextId: 't1',
      texts: [{ id: 't1', filename: 'a.txt' }],
      cards: [CARD],
      currentCard: CARD,
      identifiedChars: [{ name: '角色甲', importance: 'main', reason: '主角' }],
    })
    return renderAs(role, <CharCard />).container
  }

  it('识别/蒸馏操作区：guest 没有 .char-sidebar-actions，user 有', () => {
    expect(renderCharCard('guest').querySelector('.char-sidebar-actions')).toBeNull()
    expect(renderCharCard('user').querySelector('.char-sidebar-actions')).toBeInTheDocument()
  })

  it('识别结果里的「蒸馏角色」：guest 没有 .char-identified-btn，user 有', () => {
    expect(renderCharCard('guest').querySelector('.char-identified-btn')).toBeNull()
    expect(renderCharCard('user').querySelector('.char-identified-btn')).toBeInTheDocument()
  })

  it('回收站：guest 看不到「恢复」「彻底删除」「清空回收站」，user 都在', async () => {
    const openTrash = async (role) => {
      vi.mocked(fetchWithTimeout).mockResolvedValue({
        ok: true, status: 200,
        json: () => Promise.resolve([{ id: 'd1', name: '已删角色' }]),
      })
      const c = renderCharCard(role)
      fireEvent.click(c.querySelector('.char-sidebar-head button'))
      await waitFor(() => expect(c.querySelector('.char-list')).toBeInTheDocument())
      return labels(c, '.char-list button')
    }
    expect(await openTrash('guest')).toEqual([])
    expect(await openTrash('user')).toEqual(['恢复', '彻底删除', '清空回收站'])
  })

  it('角色卡底部：guest 没有「编辑」「分享到市场」，只读的导出/开始对话保留', () => {
    const footer = (role) => labels(renderCharCard(role), '.card-footer button')
    const guest = footer('guest')
    expect(guest).not.toContain('编辑')
    expect(guest).not.toContain('分享到市场')
    expect(guest).toContain('导出角色卡')
    expect(guest).toContain('开始对话')

    const user = footer('user')
    expect(user).toContain('编辑')
    expect(user).toContain('分享到市场')
    expect(user).toContain('导出角色卡')
    expect(user).toContain('开始对话')
  })

  it('头像上传：guest 不是可点的 button，也没有相机浮层；user 是', () => {
    const guest = renderCharCard('guest')
    expect(guest.querySelector('button.card-avatar-btn')).toBeNull()
    expect(guest.querySelector('.card-avatar-btn')).toBeInTheDocument()
    expect(guest.querySelector('.card-avatar-overlay')).toBeNull()

    const user = renderCharCard('user')
    expect(user.querySelector('button.card-avatar-btn')).toBeInTheDocument()
    expect(user.querySelector('.card-avatar-overlay')).toBeInTheDocument()
  })
})

describe('市场页：游客点不了赞、写不了评论、用不了角色', () => {
  const CARD = { id: 'm1', name: '角色甲', card_json: '{"name":"角色甲"}', author_name: '甲', likes: 3 }

  const renderMarket = async (role) => {
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      const body = url.startsWith('/api/market/list') ? { cards: [CARD], total: 1 }
        : url === '/api/market/tags' ? { tags: [] }
          : {}
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    })
    const { container } = renderAs(role, <MarketPage />)
    await waitFor(() => expect(container.querySelector('.market-card-v2')).toBeInTheDocument())
    return container
  }

  // 评论抽屉 / fork 弹窗在本组件里没有调用点（openComments、handleUse 从未被引用），
  // 对游客和用户都不可达，因此只保留点赞这一条可测落点 —— 那两处已单独报告。
  it('点赞按钮：guest 没有 .market-like-btn，user 有', async () => {
    expect((await renderMarket('guest')).querySelector('.market-like-btn')).toBeNull()
    expect((await renderMarket('user')).querySelector('.market-like-btn')).toBeInTheDocument()
  })
})

describe('市场卡详情：游客没有点赞 / 使用 / 评论输入 / 作者操作', () => {
  const CARD = { id: 'm1', user_id: 'u1', name: '角色甲', card_json: '{"name":"角色甲"}', visibility: 'public', likes: 3 }

  const renderDetail = async (role) => {
    mutate({ currentMarketCardId: 'm1' })
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      const body = url.endsWith('/detail') ? CARD : {}
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    })
    const { container } = renderAs(role, <MarketCardDetail />)
    await waitFor(() => expect(container.querySelector('.market-detail-name')).toBeInTheDocument())
    return container
  }

  it('互动区：guest 没有点赞/使用/评论输入，user 都有', async () => {
    const guest = await renderDetail('guest')
    expect(guest.querySelector('.market-detail-like-btn')).toBeNull()
    expect(guest.querySelector('.market-detail-use-btn')).toBeNull()
    expect(guest.querySelector('.market-detail-fixed-input')).toBeNull()
    expect(guest.querySelector('.market-detail-comments')).toBeInTheDocument() // 只读评论仍在

    const user = await renderDetail('user')
    expect(user.querySelector('.market-detail-like-btn')).toBeInTheDocument()
    expect(user.querySelector('.market-detail-use-btn')).toBeInTheDocument()
    expect(user.querySelector('.market-detail-fixed-input')).toBeInTheDocument()
  })

  it('作者操作区（本人查看）：guest 没有编辑/删除/换封面，user 都有', async () => {
    const guest = await renderDetail('guest')
    expect(guest.querySelector('[title="编辑"]')).toBeNull()
    expect(guest.querySelector('[title="删除"]')).toBeNull()
    expect(guest.querySelector('button.card-avatar-btn')).toBeNull()
    expect(guest.querySelector('.market-detail-comment-actions')).toBeNull()

    const user = await renderDetail('user')
    expect(user.querySelector('[title="编辑"]')).toBeInTheDocument()
    expect(user.querySelector('[title="删除"]')).toBeInTheDocument()
    expect(user.querySelector('button.card-avatar-btn')).toBeInTheDocument()
  })
})

describe('动态卡片：游客点不了赞、发不了评论、删不了动态', () => {
  const POST = { id: 'p1', content: '你好', user_id: 'u1', author_name: '甲', likes: 2, comment_count: 1 }

  const renderPost = (role, showDelete = false) => {
    const { container } = renderAs(role, <PostCard post={POST} showDelete={showDelete} />)
    return container
  }

  it('操作行：guest 只剩评论（读取），user 有赞和评', () => {
    expect(renderPost('guest').querySelectorAll('.post-card-action-btn').length).toBe(1)
    expect(renderPost('user').querySelectorAll('.post-card-action-btn').length).toBe(2)
  })

  it('删除：guest 看不到（即使 showDelete），user 看得到', () => {
    expect(renderPost('guest', true).querySelector('.post-card-delete')).toBeNull()
    expect(renderPost('user', true).querySelector('.post-card-delete')).toBeInTheDocument()
  })

  it('评论输入行：guest 没有，user 有', () => {
    const open = (role) => {
      const c = renderPost(role)
      fireEvent.click(c.querySelectorAll('.post-card-action-btn')[c.querySelectorAll('.post-card-action-btn').length - 1])
      return c
    }
    expect(open('guest').querySelector('.post-card-comment-input-row')).toBeNull()
    expect(open('user').querySelector('.post-card-comment-input-row')).toBeInTheDocument()
  })
})

describe('我的页：游客没有回收站入口 / 换封面 / 换头像 / 发动态', () => {
  const renderMine = async (role, { mobile = false } = {}) => {
    window.innerWidth = mobile ? 500 : 1024
    mutate({ currentView: 'mine', authorUserId: null, userBanner: null })
    vi.mocked(fetchWithTimeout).mockImplementation((url) => {
      const body = url.startsWith('/api/market/author/') ? { cards: [], texts: [] } : {}
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    })
    const { container } = renderAs(role, <MinePage />)
    await waitFor(() => expect(container.querySelector('.mine-profile-section')).toBeInTheDocument())
    return container
  }

  // 快捷入口只在移动端渲染，所以这条走 500px 布局
  it('快捷入口：guest 没有「回收站」，user 有', async () => {
    expect(labels(await renderMine('guest', { mobile: true }), '.entry-grid-label')).not.toContain('回收站')
    expect(labels(await renderMine('user', { mobile: true }), '.entry-grid-label')).toContain('回收站')
  })

  it('资料区：guest 没有「更换封面」/头像上传/发动态入口', async () => {
    const guest = await renderMine('guest')
    expect(guest.querySelector('.mine-banner-upload')).toBeNull()
    expect(guest.querySelector('.mine-avatar-overlay')).toBeNull()
    expect(guest.querySelector('.mine-composer')).toBeNull()
    expect(guest.querySelector('.mine-card-menu-btn')).toBeNull()

    const user = await renderMine('user')
    expect(user.querySelector('.mine-banner-upload')).toBeInTheDocument()
    expect(user.querySelector('.mine-avatar-overlay')).toBeInTheDocument()
  })
})

describe('蒸馏任务条：游客没有取消 / 继续 / 重新蒸馏，本地「关闭」保留', () => {
  const TASKS = [
    { id: 'k1', textId: 't1', character: '甲', status: 'running', done: false, progress_pct: 30, actions: ['cancel'] },
    { id: 'k2', textId: 't2', character: '乙', status: 'interrupted', done: true, progress_pct: 0, actions: ['resume', 'retry'] },
    { id: 'k3', textId: 't3', character: '丙', status: 'done', done: true, progress_pct: 100, card_id: 'card3', actions: [] },
  ]

  const renderBar = (role) => {
    mutate({ distillTasks: TASKS })
    const { container } = renderAs(role, <DistillTaskBar />)
    fireEvent.click(container.querySelector('.distill-fab'))
    return container
  }

  it('guest：取消与继续/重蒸都不在，本地关闭仍在', () => {
    const c = renderBar('guest')
    expect(c.querySelectorAll('.distill-panel-body .distill-task-item').length).toBe(3)
    expect(c.querySelector('[title="取消蒸馏"]')).toBeNull()
    expect(c.querySelector('[title="继续蒸馏"]')).toBeNull()
    expect(c.querySelector('[title="重新蒸馏"]')).toBeNull()
    expect(c.querySelector('[title="关闭"]')).toBeInTheDocument()
  })

  it('user：取消与继续/重蒸都在', () => {
    const c = renderBar('user')
    expect(c.querySelector('[title="取消蒸馏"]')).toBeInTheDocument()
    expect(c.querySelector('[title="继续蒸馏"]')).toBeInTheDocument()
    expect(c.querySelector('[title="重新蒸馏"]')).toBeInTheDocument()
  })
})
