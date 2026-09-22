/**
 * 「这条没存上」的唯一呈现 —— 一对一与群聊、用户消息与角色回复，文案与样式都只有这一份。
 *
 * 文案写死、不接受 props：四个落点各写各的，早晚会出现「未保存」「没保存」「刷新会丢」
 * 三种说法，而用户要的是同一个判断。要改口径就改这里。
 */
export default function UnsavedHint() {
  return <span className="unsaved-hint">未保存，刷新后会丢失</span>
}
