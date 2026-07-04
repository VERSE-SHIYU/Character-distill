export const QUICK_ENTRIES = [
  { key: 'messages', label: '消息',   icon: 'mail',     view: 'messages', badge: 'unread' },
  { key: 'settings', label: '设置',   icon: 'settings', view: 'settings' },
  { key: 'theme',    label: '主题',   icon: 'palette',  action: 'themeDrawer' },
  { key: 'market',   label: '市场',   icon: 'globe',    view: 'market' },
]

export const SETTINGS_ENTRIES = [
  { key: 'profile', label: '个人资料', icon: 'user',     view: 'profile' },
  { key: 'voice',   label: '语音',     icon: 'mic',     view: 'voice' },
  { key: 'admin',   label: '管理面板', icon: 'dashboard', view: 'admin', requires: 'isAdmin' },
  { key: 'legal',   label: '法律条款', icon: 'shield',   view: 'legal' },
]
