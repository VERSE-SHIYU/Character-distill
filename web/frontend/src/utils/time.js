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

  // Normalize both to start of natural day (local midnight)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const targetStart = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const dayDiff = Math.round((todayStart - targetStart) / 86400000)

  if (dayDiff === 0) return hhmm
  if (dayDiff === 1) return `昨天 ${hhmm}`
  if (dayDiff === 2) return `前天 ${hhmm}`
  if (dayDiff >= 3 && dayDiff <= 6) return `${WEEKDAYS[d.getDay()]} ${hhmm}`

  // >= 7 natural days
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
}
