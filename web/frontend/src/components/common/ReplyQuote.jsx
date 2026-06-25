export default function ReplyQuote({ preview, messageId, onScrollTo }) {
  if (!messageId || !preview) return null

  const speaker = preview.split(':')[0]
  const text = preview.split(':').slice(1).join(':')

  return (
    <div className="msg-reply-quote" onClick={() => onScrollTo?.(messageId)}>
      <div className="msg-reply-quote-speaker">{speaker}</div>
      <div className="msg-reply-quote-text">{text}</div>
    </div>
  )
}
