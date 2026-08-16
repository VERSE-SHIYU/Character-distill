// identity 字段两种合法形态：字符串（一句话身份）或 { relationships: {...} }（角色预设）。
// 畸形数据会把 { name, description } 塞进来，直接渲染成文本触发 React #31。无 relationships 的对象归一成字符串。
function normalizeIdentity(identity) {
  if (typeof identity !== 'object' || identity === null || identity.relationships) return identity
  return typeof identity.name === 'string' ? identity.name
    : (typeof identity.description === 'string' ? identity.description : '')
}

export function parseCardJson(card) {
  let out
  if (!card) return {}
  if (typeof card === 'string') {
    try { out = JSON.parse(card) } catch { return {} }
  } else if (typeof card.card_json === 'string') {
    try { out = JSON.parse(card.card_json) } catch { return {} }
  } else {
    out = card.card_json || card
  }
  if (out && typeof out === 'object' && typeof out.identity === 'object' && out.identity !== null) {
    const id = normalizeIdentity(out.identity)
    if (id !== out.identity) out = { ...out, identity: id }
  }
  return out
}
