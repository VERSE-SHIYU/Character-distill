/**
 * 后端那条消息的落库状态贴到前端消息对象上。
 *
 * 后端只给一个字段：`save`。**落库了就不带它**，没落库时带 `{state, key}` ——
 * `state` 是 `pending`（库不可达，还留在队里，下次写/重试/关停会补上）或
 * `failed`（库可达但这条写不进去，重试过一次后放弃）。这里把它翻成
 * `saveState` / `saveKey`：前者决定提示怎么显示，后者是补写回来后认领真 id 的凭据。
 *
 * 为什么只认「有 save」、不认 falsy：`save` 缺省是有含义的 —— 落库了、`hidden` 的
 * 用户消息（压根没有这条）、摘要帧、旧接口都不会带它。把它们一律当失败，用户会看到
 * 满屏「未保存」。只有后端**明说**没落库才算数。
 *
 * 不就地改：传进来的是 store 里正在渲染的对象，就地打标会让 React 看不见变化（同一个
 * 引用）。返回新对象、没 save 时原样返回 `msg`。
 */

export function withSaveResult(msg, save) {
  return save ? { ...msg, saveState: save.state, saveKey: save.key } : msg
}

/**
 * 当前还没落库的那些消息的幂等键 —— 点「重试」时带回后端去对账。
 *
 * 只收 `pending`：`failed` 的那条后端已经放弃了，问也是白问。后端队列在内存里，
 * 会话被空闲清理逐出后它就空了，而库里可能早就有这条消息（写成功了、只是响应没回到
 * 前端）—— 带上 key，后端才答得上「它到底存没存」；不带的话那条消息永远停在
 * 「未保存」。
 *
 * 没有未保存的消息时回空数组：空 keys 就是「只补写」，与加对账之前一字不差。
 */
export function pendingSaveKeys(messages) {
  return (messages || [])
    .filter((m) => m.saveState === 'pending' && m.saveKey)
    .map((m) => m.saveKey)
}

/**
 * 把一次补写的读数落到消息列表上 —— 一对一与群聊共用这一份。
 *
 * 读数形状：`{flushed: [{key, id}], dropped: [key]}`。补上的按 key 填回真 id 并清掉
 * 「未保存」；判死的按 key 翻成 `failed`（前端不再给重试按钮 —— 重试也没用）。
 * 认不出的 key 说明那条消息已经不在列表里（被撤回/换会话了），原样跳过。
 *
 * 不就地改：返回新数组，没被这次读数提到的消息保持**同一个引用**。
 */
export function applyFlushReport(messages, report) {
  const flushed = report?.flushed || []
  const dropped = report?.dropped || []
  if (!flushed.length && !dropped.length) return messages

  const idByKey = new Map(flushed.map((f) => [f.key, f.id]))
  const droppedKeys = new Set(dropped)
  return messages.map((m) => {
    if (!m.saveKey) return m
    if (idByKey.has(m.saveKey)) {
      const { saveState, saveKey, ...rest } = m
      return { ...rest, id: idByKey.get(m.saveKey) ?? m.id }
    }
    if (droppedKeys.has(m.saveKey)) return { ...m, saveState: 'failed' }
    return m
  })
}
