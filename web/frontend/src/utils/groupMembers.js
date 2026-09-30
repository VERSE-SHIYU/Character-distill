// 群聊成员规则的前端一侧：只能选同一本书里的不同角色（书由「先选书」那一步保证）。
// 同一角色的多个版本只能选一张 —— 后端 `routers/group.py::_check_group_members` 同一规则，
// 这里只负责让用户在选的时候就看到，不替代后端判定。

const cardId = (c) => c.id || c.card_id

/** 这张卡是否因「同一角色的另一个版本已选」而不能再选。已选中的卡永远可以取消。 */
export function blockedBySameCharacter(card, selectedIds, cards) {
  if (selectedIds.includes(cardId(card))) return false
  return cards.some((c) => selectedIds.includes(cardId(c)) && c.name === card.name)
}
