import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import ReactMarkdown from 'react-markdown'
import termsMd from '../legal/terms_v5.md?raw'
import privacyMd from '../legal/privacy_v3.md?raw'

export default function LegalPage() {
  const legalTab = useAppStore((s) => s.legalTab)
  const setLegalTab = useAppStore((s) => s.setLegalTab)
  const isLoggedIn = useAppStore((s) => s.isLoggedIn)
  const setView = useAppStore((s) => s.setView)
  const [termsContent] = useState(termsMd)
  const [privacyContent] = useState(privacyMd)

  const handleBack = () => setView(isLoggedIn ? 'home' : 'login')

  return (
    <div className="legal-page">
      <header className="panel-header">
        <button type="button" className="chat-back-btn" onClick={handleBack} title="返回">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        <h1 className="panel-title">法律条款</h1>
      </header>

      <div className="legal-tabs">
        <button
          className={`legal-tab${legalTab === 'terms' ? ' active' : ''}`}
          onClick={() => setLegalTab('terms')}
        >
          用户协议
        </button>
        <button
          className={`legal-tab${legalTab === 'privacy' ? ' active' : ''}`}
          onClick={() => setLegalTab('privacy')}
        >
          隐私政策
        </button>
      </div>

      <div className="legal-content">
        {legalTab === 'terms' ? (
          <ReactMarkdown>{termsContent}</ReactMarkdown>
        ) : (
          <ReactMarkdown>{privacyContent}</ReactMarkdown>
        )}
      </div>
    </div>
  )
}
