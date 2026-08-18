import { TabBar, SafeArea } from 'antd-mobile'
import useAppStore from '../store/useAppStore'
import { SECONDARY_VIEWS } from '../config/navigation'
import { Home, Edit3, Users, User } from './common/Icon'

const tabs = [
  {
    key: 'home',
    title: '首页',
    icon: <Home size={22} />,
  },
  {
    key: 'text',
    title: '创作',
    icon: <Edit3 size={22} />,
  },
  {
    key: 'groupChat',
    title: '群聊',
    icon: <Users size={22} />,
  },
  {
    key: 'mine',
    title: '我的',
    icon: <User size={22} />,
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
