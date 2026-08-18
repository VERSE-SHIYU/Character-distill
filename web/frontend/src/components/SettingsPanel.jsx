import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import EntryList from './common/EntryList'
import PageHeader from './PageHeader'
import useSwipeBack from '../hooks/useSwipeBack'
import ThemeDrawer from './common/ThemeDrawer'
import ConfirmModal from './common/ConfirmModal'
import { SETTINGS_ENTRIES } from '../config/mineEntries'
import { LogIn, ChevronRight } from './common/Icon'

export default function SettingsPanel() {
  const popView = useAppStore((s) => s.popView)
  const pushView = useAppStore((s) => s.pushView)
  const authUser = useAppStore((s) => s.authUser)
  const logout = useAppStore((s) => s.logout)
  const [themeOpen, setThemeOpen] = useState(false)
  const [logoutConfirm, setLogoutConfirm] = useState(false)

  const handleAction = (key, view) => {
    if (key === 'themeDrawer') { setThemeOpen(true); return }
    if (view) pushView(view)
  }

  const swipeBack = useSwipeBack(popView)

  return (
    <div className="settings-panel panel" {...swipeBack}>
      <header className="panel-header">
        <PageHeader title="设置" onBack={popView} />
      </header>

      <EntryList entries={SETTINGS_ENTRIES} flags={{ isAdmin: authUser?.is_admin }} onAction={handleAction} />
      <div className="entry-group-gap" />

      <button type="button" className="entry-list-item settings-logout-btn" onClick={() => setLogoutConfirm(true)}>
        <span className="entry-list-icon-wrap">
          <LogIn size={20} />
        </span>
        <span className="entry-list-label">退出登录</span>
        <ChevronRight size={16} className="entry-list-arrow" />
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
