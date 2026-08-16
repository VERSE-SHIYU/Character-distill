import { TabBar, SafeArea } from 'antd-mobile'
import useAppStore from '../store/useAppStore'
import { SECONDARY_VIEWS } from '../config/navigation'

const tabs = [
  {
    key: 'home',
    title: '首页',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={22} height={22}>
        <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </svg>
    ),
  },
  {
    key: 'text',
    title: '创作',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={22} height={22}>
        <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />
      </svg>
    ),
  },
  {
    key: 'groupChat',
    title: '群聊',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={22} height={22}>
        <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 00-3-3.87" />
        <path d="M16 3.13a4 4 0 010 7.75" />
      </svg>
    ),
  },
  {
    key: 'mine',
    title: '我的',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={22} height={22}>
        <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    ),
  },
]

const VIEW_GROUPS = {
  home: ['home', 'feed'],
  text: ['text', 'character'],
  groupChat: ['groupChat'],
  mine: ['mine', 'messages', 'market', 'author', 'textDetail', 'marketCardDetail', 'admin', 'profile', 'settings', 'trash', 'legal', 'voice', 'reader', 'history'],
}

export default function MobileTabBar() {
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const inConversation = useAppStore((s) => s.inConversation)
  const unreadTotal = useAppStore((s) => s.unreadTotal)

  if (SECONDARY_VIEWS.includes(currentView) || inConversation) return null

  const activeKey = Object.keys(VIEW_GROUPS).find((k) => VIEW_GROUPS[k].includes(currentView))

  return (
    <div className="mobile-tabbar">
      <TabBar activeKey={activeKey} onChange={(key) => setView(key)}>
        {tabs.map(({ key, title, icon }) => (
          <TabBar.Item
            key={key}
            icon={(
              <div className="tab-icon-wrap">
                {key === 'mine' && unreadTotal > 0 ? (
                  <span className="tab-badge-wrap">
                    {icon}
                    <span className="tab-badge-dot" />
                  </span>
                ) : icon}
                <i className="tab-dot" aria-hidden="true" />
              </div>
            )}
            title={title}
          />
        ))}
      </TabBar>
      <SafeArea position="bottom" />
    </div>
  )
}
