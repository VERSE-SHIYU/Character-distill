export function parseCardJson(card) {
  if (!card) return {}
  if (typeof card === 'string') {
    try { return JSON.parse(card) } catch { return {} }
  }
  if (typeof card.card_json === 'string') {
    try { return JSON.parse(card.card_json) } catch { return {} }
  }
  return card.card_json || card
}
