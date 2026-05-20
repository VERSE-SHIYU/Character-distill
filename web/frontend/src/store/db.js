import { openDB } from 'idb'
import { getAuthHeaders } from '../api/client'

const DB_NAME = 'character_sim'
const DB_VERSION = 1

function getDB() {
  return openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains('avatars')) {
        db.createObjectStore('avatars')
      }
    },
  })
}

export async function saveAvatar(id, data) {
  const db = await getDB()
  await db.put('avatars', data, id)
}

export async function getAvatar(id) {
  const db = await getDB()
  return (await db.get('avatars', id)) ?? null
}

export async function deleteAvatar(id) {
  const db = await getDB()
  await db.delete('avatars', id)
}

/**
 * Load avatar for a card — try IndexedDB first, then backend API.
 * Returns a data URL string or null.
 */
export async function loadCardAvatar(cardId) {
  if (!cardId) return null
  // Try local IndexedDB cache first
  const blob = await getAvatar(cardId)
  if (blob) {
    return new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = () => resolve(null)
      reader.readAsDataURL(blob)
    })
  }
  // Fallback to backend
  try {
    const res = await fetch(`/api/cards/${cardId}/avatar`, { headers: { ...getAuthHeaders() } })
    if (res.ok) {
      const { data } = await res.json()
      if (data) return data
    }
  } catch { /* no backend or no avatar saved */ }
  return null
}
