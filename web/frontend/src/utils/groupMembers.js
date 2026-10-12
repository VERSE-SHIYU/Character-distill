// 群聊成员规则的前端一侧：只能选同一本书里的不同角色（书由「先选书」那一步保证）。
// 同一角色的多个版本只能选一张 —— 后端 `routers/group.py::_check_group_members` 同一规则，
// 这里只负责让用户在选的时候就看到，不替代后端判定。

import { arcPhaseOptions, effectiveArcPhase } from './card'

const cardId = (c) => c.id || c.card_id

/** 这张卡是否因「同一角色的另一个版本已选」而不能再选。已选中的卡永远可以取消。 */
export function blockedBySameCharacter(card, selectedIds, cards) {
  if (selectedIds.includes(cardId(card))) return false
  return cards.some((c) => selectedIds.includes(cardId(c)) && c.name === card.name)
}

/** 「谁是 AI 成员」的前端唯一一处：扮演的卡（`personaType === 'character'` 时的 `personaCardId`）剔除，其余原序。 */
export function aiMemberIds(cardIds, personaType, personaCardId) {
  return cardIds.filter((id) => !(personaType === 'character' && id === personaCardId))
}

/** 群会话的 `card_ids` 可能是数组或 JSON 字符串（两个组件原来各写一份）。 */
export function parseCardIds(raw) {
  if (Array.isArray(raw)) return raw
  try { return JSON.parse(raw || '[]') } catch { return [] }
}

/** 建群第 2 步的选时期列表（渲染与请求体都只读它）：AI 成员里「可选阶段」的卡各一项。
 *
 * `lookup(id)` 取卡（调用方决定数据源）；`avatarOf(id)` 取头像。扮演的那张卡、不可选 /
 * 无阶段的卡都不进列表；`memberPhases[id]` 没改过 → 用 `effectiveArcPhase`（最后阶段）。
 * 纯函数：不读 store、不发请求，`groupMembers.test.js` 直接喂数据验。 */
export function groupPhaseChoices({ cardIds, personaType, personaCardId, memberPhases = {}, lookup, avatarOf }) {
  return aiMemberIds(cardIds, personaType, personaCardId)
    .map((id) => {
      const card = lookup(id)
      if (!card) return null
      const options = arcPhaseOptions(card)
      if (options.length === 0) return null
      return {
        cardId: id, card, options,
        value: memberPhases[id] ?? effectiveArcPhase(card, null),
        avatar: avatarOf ? avatarOf(id) : undefined,
      }
    })
    .filter(Boolean)
}

/** 请求体的 `member_phases`：由 `groupPhaseChoices` 的结果生成（卡 id → 阶段号）。 */
export function memberPhasesPayload(choices) {
  return Object.fromEntries(choices.map((c) => [c.cardId, c.value]))
}
