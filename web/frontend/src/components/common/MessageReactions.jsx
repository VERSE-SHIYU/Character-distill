import { MessageSquare } from './Icon'

const DEFAULT_QUICK_EMOJIS = ['👍','❤️','😂','😮','😢','🔥']

export default function MessageReactions({
  side,
  reactions = [],
  quickEmojis = DEFAULT_QUICK_EMOJIS,
  showQuickBar = false,
  onReact,
  onReply,
  authUserId,
  renderUserLabel,
}) {
  const defaultRenderUserLabel = (users) => {
    return [...new Set(users || [])]
      .map(uid =>
        uid === authUserId ? '你' :
        uid?.startsWith?.('char:') ? uid.slice(5) :
        '其他用户'
      )
      .join('、')
  }

  const getUserLabel = renderUserLabel || defaultRenderUserLabel

  return (
    <>
      {reactions.length > 0 && (
        <div className="msg-reactions">
          {reactions.map((r, ri) => (
            <button key={ri} type="button"
              className={`msg-reaction-badge${r.users?.includes(authUserId || '') ? ' mine' : ''}`}
              title={getUserLabel(r.users)}
              onClick={() => onReact?.(r.emoji)}>
              {r.emoji} {r.count}
            </button>
          ))}
        </div>
      )}

      {showQuickBar && onReact && (
        <div className="msg-quick-reactions msg-reactions-bar" data-side={side}>
          {onReply && (
            <button type="button" className="msg-action-btn" title="引用回复"
              onClick={onReply}>
              <MessageSquare size={14} />
            </button>
          )}
          {quickEmojis.map(e => (
            <button key={e} type="button" className="msg-quick-reaction-btn"
              onClick={() => onReact(e)}>{e}</button>
          ))}
        </div>
      )}
    </>
  )
}
