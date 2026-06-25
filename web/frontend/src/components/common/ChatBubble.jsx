import Avatar from './Avatar'

/**
 * Unified chat bubble — left (incoming) / right (outgoing) mirror.
 *
 * Props:
 *   side       — 'left' | 'right'
 *   children   — bubble body content
 *   avatar     — <Avatar /> node (rendered on the outer side of the row)
 *   name       — speaker name (rendered above bubble)
 *   time       — formatted time string
 *   status     — node rendered between avatar and bubble col (e.g. send status indicator)
 *   onNameClick — click handler for the name text
 *   className  — extra class on .cbubble-row
 *   style      — inline styles on .cbubble-row
 *
 * CSS custom properties (override per page):
 *   --cbubble-bg        background (left defaults to glass-bg, right to accent)
 *   --cbubble-border    border color (left defaults to glass-border, right to transparent)
 *   --cbubble-color     text color
 *   --cbubble-name-clr  name text color
 */
export default function ChatBubble({
  side = 'left',
  children,
  avatar,
  name,
  time,
  status,
  onNameClick,
  className = '',
  style,
}) {
  return (
    <div className={`cbubble-row cbubble-row--${side}${className ? ' ' + className : ''}`} style={style}>
      {avatar}
      {status}
      <div className="cbubble-col">
        {name && (
          <div className="cbubble-name" onClick={onNameClick}>
            {name}
          </div>
        )}
        <div className={`cbubble cbubble--${side}`}>
          <div className="cbubble-body">{children}</div>
          {time && <div className="cbubble-time">{time}</div>}
        </div>
      </div>
    </div>
  )
}
