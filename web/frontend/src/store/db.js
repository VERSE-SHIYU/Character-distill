import { openDB } from 'idb'

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
