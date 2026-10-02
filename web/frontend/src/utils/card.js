// identity 字段两种合法形态：字符串（一句话身份）或 { relationships: {...} }（角色预设）。
// 畸形数据会把 { name, description } 塞进来，直接渲染成文本触发 React #31。无 relationships 的对象归一成字符串。
function normalizeIdentity(identity) {
  if (typeof identity !== 'object' || identity === null || identity.relationships) return identity
  return typeof identity.name === 'string' ? identity.name
    : (typeof identity.description === 'string' ? identity.description : '')
}

// character_arc 的旧形态是字符串数组（每阶段一句）；现在是 { axis, phases: [{ label, state, behaviors }] }。
// 全站只认后一种 —— 在这里把旧形态转一次，渲染与编辑都不再判形态。不是数组的原样返回。
export function normalizeArc(arc) {
  if (!Array.isArray(arc)) return arc
  return { axis: '', phases: arc.map((s) => ({ label: '', state: String(s) })) }
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
  if (out && typeof out === 'object') {
    const arc = normalizeArc(out.character_arc)
    if (arc !== out.character_arc) out = { ...out, character_arc: arc }
  }
  return out
}
