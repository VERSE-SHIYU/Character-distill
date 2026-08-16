// Views that are rendered as "secondary" (push/pop stack) rather than primary tab views.
// Secondary views get the `is-secondary-view` CSS class and should hide the MobileTabBar.
export const SECONDARY_VIEWS = [
  'chat', 'character', 'author', 'textDetail',
  'marketCardDetail', 'profile', 'settings', 'apiConfig', 'trash', 'admin', 'legal', 'voice', 'reader',
  'messages', 'history', 'groupChat', 'feed', 'market', 'distillWorkbench',
]

// Fallback view when popView is called with an empty history stack
export const FALLBACK = {
  chat: 'text', groupChat: 'text', character: 'text', reader: 'text',
  author: 'market', textDetail: 'market', marketCardDetail: 'market',
  profile: 'mine', settings: 'mine', apiConfig: 'settings', trash: 'mine', admin: 'mine',
  legal: 'mine', voice: 'mine',
  messages: 'mine', history: 'mine', feed: 'home', market: 'mine',
  distillWorkbench: 'text',
}
