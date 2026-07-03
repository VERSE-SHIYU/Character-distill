/**
 * resolveOpeningMessages — 唯一开场白决策出口
 *
 * 优先级（高→低）：
 *   1. sessionLastMessage   — 续接已有会话（enterArchive）
 *   2. backendFirstMessage  — 后端本次生成/返回（startChat / createNewArchive）
 *   3. cardFirstMessage     — 卡片本地兜底（resetChat / 后端无返回时）
 *
 * @param {{ sessionLastMessage?:string, backendFirstMessage?:string, cardFirstMessage?:string }} opts
 * @param {(msg:{role:string,content:string})=>{role:string,content:string,_cid:string}} withCid
 * @returns {{role:'char',content:string,_cid:string}[]}
 */
export function resolveOpeningMessages({
  sessionLastMessage,
  backendFirstMessage,
  cardFirstMessage,
}, withCid) {
  const content = sessionLastMessage || backendFirstMessage || cardFirstMessage
  return content ? [withCid({ role: 'char', content })] : []
}
