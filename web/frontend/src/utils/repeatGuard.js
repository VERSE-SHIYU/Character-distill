/**
 * 重复消息拦截守卫。
 * 检测用户连续发送高度相似的消息，达到阈值时建议拦截。
 *
 * @param {string} text        用户正要发送的消息
 * @param {Array}  messages    当前对话消息列表（不含本条）
 * @param {number} threshold   连续相似次数阈值，默认 5
 * @returns {{ blocked: boolean, message?: string }}
 */
export function checkRepeat(text, messages, threshold = 5) {
  const normalized = text.trim()
  if (!normalized) return { blocked: false }

  const userMsgs = messages.filter((m) => m.role === 'user')

  let consecutive = 0
  for (let i = userMsgs.length - 1; i >= 0; i--) {
    const prev = (userMsgs[i].content || '').trim()
    if (prev === normalized) {
      consecutive++
    } else {
      break
    }
  }

  if (consecutive >= threshold - 1) {
    return { blocked: true, message: '你好像在重复发送相同的内容~' }
  }
  return { blocked: false }
}
