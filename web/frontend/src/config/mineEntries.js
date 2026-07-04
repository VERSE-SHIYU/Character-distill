export const QUICK_ENTRIES = [
  { key: 'messages', label: '消息',   icon: 'mail',     badge: 'unread' },
  { key: 'history', label: '历史',   icon: 'clock',    view: 'history' },
  { key: 'trash',   label: '回收站', icon: 'trash',    view: 'trash' },
  { key: 'feed',    label: '动态',   icon: 'megaphone', view: 'feed' },
  { key: 'market',  label: '市场',   icon: 'globe',    view: 'market' },
  { key: 'settings',label: '设置',   icon: 'settings', view: 'settings' },
]

export const SETTINGS_ENTRIES = [
  { key: 'profile',     label: '个人资料', icon: 'user',      view: 'profile' },
  { key: 'themeDrawer', label: '主题',     icon: 'palette',   action: 'themeDrawer' },
  { key: 'apiConfig',   label: 'API 配置', icon: 'terminal',  view: 'apiConfig' },
  { key: 'voice',       label: '语音',     icon: 'mic',       view: 'voice' },
  { key: 'admin',       label: '管理面板', icon: 'dashboard', view: 'admin', requires: 'isAdmin' },
  { key: 'legal',       label: '法律条款', icon: 'shield',    view: 'legal' },
]
