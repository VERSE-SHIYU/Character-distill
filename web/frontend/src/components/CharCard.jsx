import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { saveAvatar, getAvatar } from '../store/db'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

// ---- CharCard (top-level) ----

export default function CharCard() {
  const currentTextId = useAppStore((s) => s.currentTextId)
  const texts = useAppStore((s) => s.texts)

  if (!currentTextId) {
    return (
      <div className="shell-placeholder">
        <div className="shell-placeholder-inner">
          <div className="shell-placeholder-icon">{'\u{1F4D6}'}</div>
          <div className="shell-placeholder-title">
            {'\u8bf7\u5148\u9009\u62e9\u6587\u672c'}
          </div>
          <div className="shell-placeholder-sub">
            {'\u5728\u201c\u6587\u672c\u7ba1\u7406\u201d\u4e2d\u4e0a\u4f20\u5e76\u9009\u4e2d\u4e00\u4e2a\u6587\u672c'}
          </div>
        </div>
      </div>
    )
  }

  const currentText = texts.find((t) => t.id === currentTextId)
  const filename = currentText?.filename || currentTextId.slice(0, 8)

  return (
    <div className="char-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">
          {'\u89d2\u8272\u7ba1\u7406'}
        </h1>
        <p className="panel-desc">
          {'\u5f53\u524d\u6587\u672c\uff1a'}{filename}
        </p>
      </header>
      <CharPanelBody textId={currentTextId} />
    </div>
  )
}

// ---- Body: cards list + detail pane ----

function CharPanelBody({ textId }) {
  const cards = useAppStore((s) => s.cards)
  const currentCard = useAppStore((s) => s.currentCard)
  const loadCards = useAppStore((s) => s.loadCards)
  const error = useAppStore((s) => s.error)
  const setError = useAppStore((s) => s.setError)

  useEffect(() => {
    loadCards(textId)
  }, [textId, loadCards])

  return (
    <div className="char-body">
      <CharSidebar textId={textId} cards={cards} currentCard={currentCard} />
      <div className="char-detail-pane">
        {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}
        {currentCard ? (
          <CardDetail card={currentCard} />
        ) : (
          <div className="char-detail-empty">
            <div className="char-detail-empty-icon">{'\u{1F464}'}</div>
            <p>{'\u9009\u62e9\u6216\u84b8\u998f\u4e00\u4e2a\u89d2\u8272\u67e5\u770b\u8be6\u60c5'}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---- Left: character list + identify/distill flow ----

function CharSidebar({ textId, cards, currentCard }) {
  const identifiedChars = useAppStore((s) => s.identifiedChars)
  const identifying = useAppStore((s) => s.identifying)
  const distilling = useAppStore((s) => s.distilling)
  const identifyCharacters = useAppStore((s) => s.identifyCharacters)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const selectCard = useAppStore((s) => s.selectCard)

  const [distillingName, setDistillingName] = useState(null)

  const handleIdentify = async () => {
    try {
      await identifyCharacters(textId)
    } catch { /* store already sets error */ }
  }

  const handleDistill = async (name) => {
    setDistillingName(name)
    try {
      await distillCharacter(textId, name)
    } catch { /* store already sets error */ }
    setDistillingName(null)
  }

  const hasCards = cards.length > 0
  const hasIdentified = identifiedChars.length > 0

  return (
    <div className="char-sidebar-inner">
      <div className="char-sidebar-head">
        <h2 className="char-sidebar-title">
          {'\u89d2\u8272\u5217\u8868'}
        </h2>
        <span className="char-sidebar-count">{cards.length}</span>
      </div>

      {/* Distilled cards list */}
      {hasCards && (
        <ul className="char-list">
          {cards.map((c) => {
            const cardData = typeof c.card_json === 'string'
              ? JSON.parse(c.card_json)
              : c.card_json || c
            const name = cardData.name || c.name
            const identity = cardData.identity || ''
            const isActive = currentCard?.id === c.id
            return (
              <li key={c.id}>
                <button
                  type="button"
                  className={`char-list-item${isActive ? ' active' : ''}`}
                  onClick={() => selectCard({ ...c, ...cardData })}
                >
                  <Avatar name={name} size={34} />
                  <div className="char-list-info">
                    <div className="char-list-name">{name}</div>
                    <div className="char-list-identity">{identity}</div>
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {/* Identified but not yet distilled */}
      {hasIdentified && (
        <div className="char-identified">
          <h3 className="char-identified-title">
            {'\u8bc6\u522b\u7ed3\u679c'}
          </h3>
          <ul className="char-identified-list">
            {identifiedChars.map((ch, i) => {
              const name = ch.name || `Character ${i + 1}`
              const already = cards.some((c) => {
                const d = typeof c.card_json === 'string'
                  ? JSON.parse(c.card_json)
                  : c.card_json || c
                return (d.name || c.name) === name
              })
              return (
                <li key={name} className="char-identified-item">
                  <div className="char-identified-info">
                    <span className="char-identified-name">{name}</span>
                    {ch.importance && (
                      <span className="char-identified-badge">
                        {ch.importance}
                      </span>
                    )}
                  </div>
                  {ch.reason && (
                    <p className="char-identified-reason">{ch.reason}</p>
                  )}
                  <button
                    type="button"
                    className="btn-primary char-identified-btn"
                    disabled={distilling || already}
                    onClick={() => handleDistill(name)}
                  >
                    {distillingName === name
                      ? '\u84b8\u998f\u4e2d\u2026'
                      : already
                        ? '\u5df2\u84b8\u998f'
                        : '\u84b8\u998f\u89d2\u8272'}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* Action buttons */}
      <div className="char-sidebar-actions">
        {!hasCards && !hasIdentified && !identifying && (
          <button
            type="button"
            className="btn-primary char-action-btn"
            onClick={handleIdentify}
          >
            {'\u5f00\u59cb\u84b8\u998f'}
          </button>
        )}
        {identifying && <Loading text={'\u6b63\u5728\u8bc6\u522b\u89d2\u8272\u2026'} />}
        {distilling && distillingName && (
          <Loading text={`\u6b63\u5728\u84b8\u998f ${distillingName}\u2026`} />
        )}
        {(hasCards || hasIdentified) && !identifying && (
          <button
            type="button"
            className="char-reidentify-btn"
            onClick={handleIdentify}
            disabled={identifying}
          >
            {'\u91cd\u65b0\u8bc6\u522b'}
          </button>
        )}
      </div>
    </div>
  )
}

// ---- Right: card detail view ----

function CardDetail({ card }) {
  const startChat = useAppStore((s) => s.startChat)
  const userRole = useAppStore((s) => s.userRole)
  const setUserRole = useAppStore((s) => s.setUserRole)

  const data = typeof card.card_json === 'string'
    ? JSON.parse(card.card_json)
    : card.card_json || card
  const name = data.name || card.name || '?'
  const style = data.speaking_style || {}
  const rels = data.relationships || []

  const [avatarUrl, setAvatarUrl] = useState(null)
  const avatarInputRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    getAvatar(card.id).then((blob) => {
      if (!cancelled && blob) {
        setAvatarUrl(URL.createObjectURL(blob))
      } else {
        setAvatarUrl(null)
      }
    })
    return () => {
      cancelled = true
    }
  }, [card.id])

  const handleAvatarChange = useCallback(
    async (e) => {
      const file = e.target.files?.[0]
      if (!file) return
      await saveAvatar(card.id, file)
      setAvatarUrl(URL.createObjectURL(file))
    },
    [card.id],
  )

  return (
    <div className="card-detail">
      <div className="card-detail-scroll">
        {/* Header: avatar + name + identity */}
        <div className="card-hero">
          <button
            type="button"
            className="card-avatar-btn"
            onClick={() => avatarInputRef.current?.click()}
            title={'\u70b9\u51fb\u4e0a\u4f20\u5934\u50cf'}
          >
            <Avatar name={name} src={avatarUrl} size={72} />
            <div className="card-avatar-overlay">{'\u{1F4F7}'}</div>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleAvatarChange}
          />
          <div className="card-hero-text">
            <h2 className="card-name">{name}</h2>
            {data.identity && (
              <p className="card-identity">{data.identity}</p>
            )}
          </div>
        </div>

        {/* Personality traits */}
        {data.personality_traits?.length > 0 && (
          <CardSection label={'\u6027\u683c\u7279\u5f81'}>
            <div className="pill-list">
              {data.personality_traits.map((t, i) => (
                <span key={i} className="pill">{t}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Speaking style */}
        {style.tone && (
          <CardSection label={'\u8bed\u8a00\u98ce\u683c'}>
            <div className="card-style-grid">
              <StyleChip label={'\u8bed\u6c14'} value={style.tone} />
              <StyleChip label={'\u53e5\u5f0f'} value={style.sentence_pattern} />
              <StyleChip label={'\u7528\u8bcd'} value={style.vocabulary_level} />
            </div>
            {style.catchphrases?.length > 0 && (
              <div className="card-catchphrases">
                {style.catchphrases.map((c, i) => (
                  <p key={i} className="catchphrase">
                    {'\u201c'}{c}{'\u201d'}
                  </p>
                ))}
              </div>
            )}
          </CardSection>
        )}

        {/* Values */}
        {data.values?.length > 0 && (
          <CardSection label={'\u6838\u5fc3\u4ef7\u503c\u89c2'}>
            <div className="pill-list">
              {data.values.map((v, i) => (
                <span key={i} className="pill pill-value">{v}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Key memories */}
        {data.key_memories?.length > 0 && (
          <CardSection label={'\u5173\u952e\u8bb0\u5fc6'}>
            <ul className="card-memory-list">
              {data.key_memories.map((m, i) => (
                <li key={i} className="card-memory-item">{m}</li>
              ))}
            </ul>
          </CardSection>
        )}

        {/* Relationships */}
        {rels.length > 0 && (
          <CardSection label={'\u4eba\u7269\u5173\u7cfb'}>
            <div className="card-rel-list">
              {rels.map((r, i) => (
                <div key={i} className="card-rel-row">
                  <span className="card-rel-target">{r.target}</span>
                  <span className="card-rel-type pill">{r.relation}</span>
                  <span className="card-rel-attitude">{r.attitude}</span>
                </div>
              ))}
            </div>
          </CardSection>
        )}

        {/* Inner tensions */}
        {data.inner_tensions?.length > 0 && (
          <CardSection label={'\u5185\u5728\u77db\u76fe'}>
            <div className="pill-list">
              {data.inner_tensions.map((t, i) => (
                <span key={i} className="pill pill-tension">{t}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Background */}
        {data.background && (
          <CardSection label={'\u80cc\u666f'}>
            <p className="card-background">{data.background}</p>
          </CardSection>
        )}

        {/* User identity input */}
        <CardSection label={'\u7528\u6237\u8eab\u4efd\u8bbe\u5b9a'}>
          <input
            type="text"
            className="card-user-role-input"
            placeholder={'\u4f8b\u5982\uff1a\u201c\u4f60\u662f\u4ed6\u7684\u65e7\u65f6\u597d\u53cb\u201d'}
            value={userRole}
            onChange={(e) => setUserRole(e.target.value)}
          />
        </CardSection>
      </div>

      {/* Bottom: export + start chat */}
      <div className="card-footer">
        <a
          href={`/api/distill/cards/${card.id}/export?format=tavern`}
          download
          className="btn-secondary card-export-btn"
        >
          {'\u{1F4E5} \u5bfc\u51fa\u89d2\u8272\u5361'}
        </a>
        <button
          type="button"
          className="btn-primary card-chat-btn"
          onClick={startChat}
        >
          {'\u{1F4AC} \u5f00\u59cb\u5bf9\u8bdd'}
        </button>
      </div>
    </div>
  )
}

// ---- Helpers ----

function CardSection({ label, children }) {
  return (
    <div className="card-section">
      <div className="card-section-label">{label}</div>
      {children}
    </div>
  )
}

function StyleChip({ label, value }) {
  if (!value) return null
  return (
    <div className="card-style-chip">
      <span className="card-style-chip-label">{label}</span>
      <span className="card-style-chip-value">{value}</span>
    </div>
  )
}
