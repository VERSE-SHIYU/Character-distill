/**
 * B1 — pilot switches. The URL is only a way to *set* the stored value
 * (?kbd=native|legacy, ?kbdcheck=1|0); everything afterwards reads the key.
 * Default: legacy, check off.
 *
 * Temporary: this whole module is deleted once every page has migrated and the
 * switches are gone.
 */

export const MODE_KEY = 'kbd_mode'
export const CHECK_KEY = 'kbd_check'

const MODES = new Set(['native', 'legacy'])

export function applyKeyboardUrlOverrides(
  search = window.location.search,
  storage = window.localStorage,
) {
  const params = new URLSearchParams(search)
  const mode = params.get('kbd')
  if (MODES.has(mode)) storage.setItem(MODE_KEY, mode)
  const check = params.get('kbdcheck')
  if (check === '1' || check === '0') storage.setItem(CHECK_KEY, check)
}

export function getKeyboardMode(storage = window.localStorage) {
  return storage.getItem(MODE_KEY) === 'native' ? 'native' : 'legacy'
}

export function isGoalCheckEnabled(storage = window.localStorage) {
  return storage.getItem(CHECK_KEY) === '1'
}
