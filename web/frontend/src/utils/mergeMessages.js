// Merge the newest page of server messages into the local message list without
// clobbering optimistic (temp / _status) messages or older loaded pages.
// Server copy wins for same-id messages unless the local copy is still pending.
export function mergeMessages(prev, serverMsgs) {
  const byId = new Map()
  for (const m of prev) byId.set(m.id, m)
  for (const m of serverMsgs) {
    const local = byId.get(m.id)
    byId.set(m.id, local && local._status ? local : m)
  }
  return [...byId.values()].sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
}
