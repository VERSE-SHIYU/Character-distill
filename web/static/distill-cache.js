/**
 * distill-cache.js — 全局蒸馏缓存层
 * 使用 IndexedDB 存储：文本指纹 → 所有角色档案 + 全文分段索引
 * 
 * 核心接口：
 *   DistillCache.get(textHash) → cachedResult | null
 *   DistillCache.set(textHash, { characters, segments, meta })
 *   DistillCache.getCharacter(textHash, charName) → charProfile | null
 *   DistillCache.listAll() → [{textHash, meta, characters}]
 *   DistillCache.clear()
 */

const DB_NAME = 'CharSimDistillCache';
const DB_VERSION = 1;
const STORE_NAME = 'distill_results';

// 生成文本指纹（取前1000字 + 长度 hash，避免存储完整文本作 key）
function textFingerprint(text) {
  const sample = text.slice(0, 1000) + '|' + text.length;
  let h = 0;
  for (let i = 0; i < sample.length; i++) {
    h = ((h << 5) - h + sample.charCodeAt(i)) | 0;
  }
  return 'tf_' + Math.abs(h).toString(36) + '_' + text.length;
}

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'textHash' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

const DistillCache = {
  // 获取某文本的全部缓存结果
  async get(textHash) {
    try {
      const db = await openDB();
      return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const req = store.get(textHash);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      });
    } catch (e) {
      console.warn('IndexedDB get 失败:', e);
      return null;
    }
  },

  // 存储全局蒸馏结果
  async set(textHash, data) {
    try {
      const db = await openDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        store.put({ textHash, ...data, cachedAt: Date.now() });
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => reject(tx.error);
      });
    } catch (e) {
      console.warn('IndexedDB set 失败:', e);
      return false;
    }
  },

  // 获取某文本中的特定角色档案
  async getCharacter(textHash, charName) {
    const result = await this.get(textHash);
    if (!result || !result.characters) return null;
    return result.characters.find(c =>
      c.name === charName || (c.aliases && c.aliases.includes(charName))
    ) || null;
  },

  // 列出所有缓存条目（用于侧边栏展示）
  async listAll() {
    try {
      const db = await openDB();
      return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const req = store.getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => resolve([]);
      });
    } catch (e) {
      console.warn('IndexedDB listAll 失败:', e);
      return [];
    }
  },

  // 清空缓存
  async clear() {
    try {
      const db = await openDB();
      return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).clear();
        tx.oncomplete = () => resolve(true);
        tx.onerror = () => resolve(false);
      });
    } catch (e) { return false; }
  },

  // 工具函数：暴露指纹算法
  fingerprint: textFingerprint,
};

window.DistillCache = DistillCache;
window.textFingerprint = textFingerprint;
