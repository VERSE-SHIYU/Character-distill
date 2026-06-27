/**
 * Return the display name for a user object.
 * Uses nickname if set, otherwise falls back to username.
 *
 * Usage: displayName(user)  =>  user.nickname || user.username
 */
export function displayName(user) {
  if (!user) return ''
  return (user.nickname || '').trim() || user.username || ''
}
