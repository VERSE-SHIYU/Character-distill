import { describe, it, expect, beforeEach } from 'vitest'
import {
  MODE_KEY,
  CHECK_KEY,
  applyKeyboardUrlOverrides,
  getKeyboardMode,
  isGoalCheckEnabled,
} from './keyboardMode'

function makeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
  }
}

describe('keyboardMode (B1)', () => {
  let storage
  beforeEach(() => {
    storage = makeStorage()
  })

  it('defaults to legacy mode when nothing is stored', () => {
    expect(getKeyboardMode(storage)).toBe('legacy')
  })

  it('defaults goal check to off when nothing is stored', () => {
    expect(isGoalCheckEnabled(storage)).toBe(false)
  })

  it('?kbd=native writes kbd_mode and is read back', () => {
    applyKeyboardUrlOverrides('?kbd=native', storage)
    expect(storage.getItem(MODE_KEY)).toBe('native')
    expect(getKeyboardMode(storage)).toBe('native')
  })

  it('?kbd=legacy writes legacy', () => {
    applyKeyboardUrlOverrides('?kbd=legacy', storage)
    expect(getKeyboardMode(storage)).toBe('legacy')
  })

  it('?kbdcheck=1 writes kbd_check and enables the check', () => {
    applyKeyboardUrlOverrides('?kbdcheck=1', storage)
    expect(storage.getItem(CHECK_KEY)).toBe('1')
    expect(isGoalCheckEnabled(storage)).toBe(true)
  })

  it('?kbdcheck=0 disables a previously enabled check', () => {
    applyKeyboardUrlOverrides('?kbdcheck=0', storage)
    expect(isGoalCheckEnabled(storage)).toBe(false)
  })

  it('ignores unknown values and leaves storage untouched', () => {
    applyKeyboardUrlOverrides('?kbd=foo&kbdcheck=yes', storage)
    expect(storage.getItem(MODE_KEY)).toBe(null)
    expect(storage.getItem(CHECK_KEY)).toBe(null)
    expect(getKeyboardMode(storage)).toBe('legacy')
    expect(isGoalCheckEnabled(storage)).toBe(false)
  })

  it('reads the persisted key when the url has no params (exit-pilot case)', () => {
    applyKeyboardUrlOverrides('?kbd=native&kbdcheck=1', storage)
    applyKeyboardUrlOverrides('', storage)
    expect(getKeyboardMode(storage)).toBe('native')
    expect(isGoalCheckEnabled(storage)).toBe(true)
  })

  it('reads both params in one call', () => {
    applyKeyboardUrlOverrides('?kbd=native&kbdcheck=1', storage)
    expect(getKeyboardMode(storage)).toBe('native')
    expect(isGoalCheckEnabled(storage)).toBe(true)
  })
})
