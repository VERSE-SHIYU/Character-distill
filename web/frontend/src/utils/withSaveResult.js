/**
 * 把「后端说这条存上没有」贴到消息对象上：**只有明确 `saved === false` 才多出 `unsaved: true`**。
 *
 * 为什么只认 `false`、不认 falsy：`saved` 缺省是有含义的。`hidden` 隐藏消息本就没入库、
 * summary 帧不带这个字段、旧接口更不会有 —— 把它们一律当失败，用户会看到满屏「未保存」。
 * 只有后端**明说** false 才算数，缺字段与 true 都是「没问题」。
 *
 * 不就地改：传进来的是 store 里正在渲染的对象，就地打标会让 React 看不见变化（同一个
 * 引用）。返回新对象、失败时也返回**新对象**，命中那条「只有失败才产生新引用」的语义。
 */

export function withSaveResult(msg, saved) {
  return saved === false ? { ...msg, unsaved: true } : msg
}
