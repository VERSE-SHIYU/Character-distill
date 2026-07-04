import useAppStore from '../store/useAppStore'

export const SECONDARY_VIEWS = [
  'chat', 'character', 'author', 'textDetail',
  'marketCardDetail', 'profile', 'settings', 'trash', 'admin', 'legal', 'voice', 'reader',
]

const TITLE_MAP = {
  chat: '聊天',
  groupChat: '群聊',
  character: '角色选择',
  author: '用户',
  textDetail: '文本详情',
  marketCardDetail: '详情',
  profile: '个人资料',
  settings: '设置',
  trash: '回收站',
  admin: '管理面板',
  legal: '协议',
  voice: '语音',
  reader: '阅读',
}

export default function MobileBackBar() {
  const currentView = useAppStore((s) => s.currentView)
  const popView = useAppStore((s) => s.popView)

  if (!SECONDARY_VIEWS.includes(currentView)) return null

  return (
    <>
      <div className="mobile-backbar">
        <button
          type="button"
          className="mobile-backbar-btn"
          onClick={popView}
          aria-label="返回"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5m7-7-7 7 7 7" />
          </svg>
        </button>
        <span className="mobile-backbar-title">{TITLE_MAP[currentView] || ''}</span>
      </div>
      <div className="mobile-backbar-placeholder" />
    </>
  )
}
