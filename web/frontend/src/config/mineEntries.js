export const QUICK_ENTRIES = [
  { key: 'messages', label: '消息', icon: 'mail', badge: 'unread' },
  { key: 'settings', label: '设置', icon: 'settings' },
  { key: 'theme',    label: '主题', icon: 'palette', action: 'themeDrawer' },
  { key: 'market',   label: '市场', icon: 'globe' },
]

export const CONTENT_ENTRIES = [
  { key: 'history',  label: '历史会话', icon: 'clock' },
  { key: 'feed',     label: '动态',     icon: 'heart' },
  { key: 'profile',  label: '个人资料', icon: 'user' },
  { key: 'voice',    label: '语音',     icon: 'mic' },
  { key: 'trash',    label: '回收站',   icon: 'trash' },
]

export const ABOUT_ENTRIES = [
  { key: 'legal', label: '法律条款', icon: 'shield' },
  { key: 'admin', label: '管理面板', icon: 'dashboard', requires: 'isAdmin' },
]
