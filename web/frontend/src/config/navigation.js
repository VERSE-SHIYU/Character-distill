// Views that are rendered as "secondary" (push/pop stack) rather than primary tab views.
// Secondary views get the MobileBackBar and should hide the MobileTabBar.
export const SECONDARY_VIEWS = [
  'chat', 'character', 'author', 'textDetail',
  'marketCardDetail', 'profile', 'settings', 'apiConfig', 'trash', 'admin', 'legal', 'voice', 'reader',
  'messages', 'history', 'groupChat',
]

// Map of secondary view → display title shown in MobileBackBar
export const TITLE_MAP = {
  chat: '聊天',
  groupChat: '群聊',
  character: '角色选择',
  author: '用户',
  textDetail: '文本详情',
  marketCardDetail: '详情',
  profile: '个人资料',
  settings: '设置',
  apiConfig: 'API 配置',
  trash: '回收站',
  admin: '管理面板',
  legal: '协议',
  voice: '语音',
  reader: '阅读',
  messages: '私信',
  history: '历史会话',
}

// Fallback view when popView is called with an empty history stack
export const FALLBACK = {
  chat: 'text', groupChat: 'text', character: 'text', reader: 'text',
  author: 'market', textDetail: 'market', marketCardDetail: 'market',
  profile: 'mine', settings: 'mine', apiConfig: 'settings', trash: 'mine', admin: 'mine',
  legal: 'mine', voice: 'mine',
  messages: 'mine', history: 'mine', feed: 'home', market: 'mine',
}
