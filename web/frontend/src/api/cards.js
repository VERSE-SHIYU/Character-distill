import { fetchWithTimeout } from './client'

// 角色卡端点与聚合的唯一入口 —— 组件不手写这些 URL。
// fetchWithTimeout 会自己带上鉴权头，调用点不需要再传。

export function fetchCardsByText(textId) {
  return fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
}

export function fetchStandaloneCards() {
  return fetchWithTimeout('/api/distill/cards/standalone')
}

/**
 * 汇总「逐文本的角色卡 + 独立卡片」成一份列表，并标上来源。
 * `_textInfo`（整条文本记录）/ `_source`（分组、筛选用）只在这里挂一次。
 * 单本拉取失败不影响其余 —— 读请求的静默 catch 是有意为之。
 */
export async function fetchAllCards(texts) {
  const all = []
  for (const t of texts) {
    try {
      const res = await fetchCardsByText(t.id)
      if (res.ok) {
        for (const c of await res.json()) {
          all.push({ ...c, _textInfo: t, _source: t.title || t.filename })
        }
      }
    } catch { /* skip failed text */ }
  }
  try {
    const res = await fetchStandaloneCards()
    if (res.ok) {
      for (const c of await res.json()) all.push({ ...c, _source: '来自市场' })
    }
  } catch { /* ignore */ }
  return all
}
