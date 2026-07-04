import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import PageHeader from './PageHeader'
import useSwipeBack from '../hooks/useSwipeBack'
import ReactMarkdown from 'react-markdown'
import termsMd from '../legal/terms_v5.md?raw'
import privacyMd from '../legal/privacy_v3.md?raw'

export default function LegalPage() {
  const legalTab = useAppStore((s) => s.legalTab)
  const setLegalTab = useAppStore((s) => s.setLegalTab)
  const isLoggedIn = useAppStore((s) => s.isLoggedIn)
  const navigateBack = useAppStore((s) => s.navigateBack)
  const [termsContent] = useState(termsMd)
  const [privacyContent] = useState(privacyMd)
  const swipeBack = useSwipeBack(navigateBack)

  return (
    <div className="legal-page" {...swipeBack}>
      <header className="panel-header">
        <PageHeader title="法律条款" onBack={navigateBack} />
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
