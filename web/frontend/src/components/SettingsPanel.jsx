import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import useIsMobile from '../hooks/useIsMobile'
import EntryList from './common/EntryList'
import ThemeDrawer from './common/ThemeDrawer'
import ConfirmModal from './common/ConfirmModal'
import { SETTINGS_ENTRIES } from '../config/mineEntries'

export default function SettingsPanel() {
  const popView = useAppStore((s) => s.popView)
  const pushView = useAppStore((s) => s.pushView)
  const authUser = useAppStore((s) => s.authUser)
  const logout = useAppStore((s) => s.logout)
  const isMobile = useIsMobile()

  const [themeOpen, setThemeOpen] = useState(false)
  const [logoutConfirm, setLogoutConfirm] = useState(false)

  const handleAction = (key, view) => {
    if (key === 'themeDrawer') { setThemeOpen(true); return }
    if (view) pushView(view)
  }

  return (
    <div className="settings-panel panel">
      <header className="panel-header">
        {!isMobile && <button type="button" className="chat-back-btn" onClick={popView} title="返回"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>返回</button>}
        <h1 className="panel-title">设置</h1>
      </header>

      <EntryList entries={SETTINGS_ENTRIES} flags={{ isAdmin: authUser?.is_admin }} onAction={handleAction} />
      <div className="entry-group-gap" />

      <button type="button" className="entry-list-item settings-logout-btn" onClick={() => setLogoutConfirm(true)}>
        <span className="entry-list-icon-wrap">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </span>
        <span className="entry-list-label">退出登录</span>
        <svg className="entry-list-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
      </button>

      <ThemeDrawer open={themeOpen} onClose={() => setThemeOpen(false)} />

      <ConfirmModal
        isOpen={logoutConfirm}
        title="退出登录"
        message="确定要退出登录吗？"
        confirmText="退出"
        onConfirm={() => { setLogoutConfirm(false); logout() }}
        onCancel={() => setLogoutConfirm(false)}
        danger
      />
    </div>
  )
}
