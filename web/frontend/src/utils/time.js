const DAY_MS = 86400000
const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

export function formatChatTime(ts) {
  if (!ts) return ''
  const now = new Date()
  let s = ts
  if (!s.includes('T')) s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
  const d = new Date(s)
  if (isNaN(d.getTime())) return ''

  const pad = (n) => String(n).padStart(2, '0')
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`

  // Today
  if (d.toDateString() === now.toDateString()) return hhmm

  // Yesterday
  const yesterday = new Date(now - DAY_MS)
  if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hhmm}`

  // Same year → weekday + time
  if (d.getFullYear() === now.getFullYear()) return `${WEEKDAYS[d.getDay()]} ${hhmm}`

  // Different year → full date + time
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hhmm}`
}
