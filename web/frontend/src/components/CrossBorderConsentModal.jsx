import { useState } from 'react'
import { createPortal } from 'react-dom'
import useAppStore from '../store/useAppStore'

export default function CrossBorderConsentModal() {
  const pending = useAppStore((s) => s.pendingCrossBorderConsent)
  const grantConsent = useAppStore((s) => s.grantCrossBorderConsent)
  const setLegalTab = useAppStore((s) => s.setLegalTab)
  const setView = useAppStore((s) => s.setView)
  const [checked, setChecked] = useState(false)

  if (!pending) return null

  const handleViewPolicy = () => {
    setLegalTab('privacy')
    setView('legal')
  }

  return createPortal(
    <div className="modal-overlay" onClick={undefined}>
      <div className="modal-card" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title" style={{ fontSize: 17 }}>跨地域数据同步说明</h3>
        <div className="modal-body" style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, maxHeight: 'calc(100vh - 320px)', overflowY: 'auto', marginTop: 8 }}>
          <p>为使不同国家/地区的用户能够在本平台相互浏览公开作品、相互发现并交流，本平台采用多地域部署架构，会在不同地域节点之间同步<strong>有限范围</strong>的信息：</p>
          <ul style={{ paddingLeft: 16, margin: '8px 0' }}>
            <li>您的<strong>公开资料</strong>（用户名、头像、所属地域）；</li>
            <li>您<strong>主动发布为公开</strong>的市场作品（含角色卡内容）；</li>
            <li><strong>经您逐次授权后</strong>向其他地域用户发送的私信；</li>
            <li>邀请码、平台管理治理所需的用户基本状态字段；</li>
            <li>您删除作品、撤回私信或注销账户时的跨地域删除指令。</li>
          </ul>
          <p><strong>核心隐私数据——对话记录、角色记忆、账户密码、AI 服务密钥——始终仅存储于您所属地域节点，不会跨地域同步。</strong></p>
          <p>您有权选择不发布公开作品、不授权跨地域私信；您注销账户时，相应的跨地域副本将一并删除。完整字段范围与保护措施详见《隐私政策》第三条。</p>
          <div className="legal-consent" style={{ marginTop: 16, marginBottom: 4 }}>
            <label className="legal-consent-label">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
              />
              <span>
                我已阅读并理解上述内容，同意本平台在不同地域节点间同步上述有限信息，
                详见<button type="button" className="legal-link-btn" onClick={handleViewPolicy}>《隐私政策》</button>
              </span>
            </label>
          </div>
        </div>
        <div className="modal-actions" style={{ marginTop: 16 }}>
          <button
            className="btn-primary"
            disabled={!checked}
            onClick={() => {
              setChecked(false)
              grantConsent()
            }}
            style={{ width: '100%' }}
          >
            同意并继续
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
