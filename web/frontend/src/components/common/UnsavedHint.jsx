/**
 * 「这条没落库」的唯一呈现 —— 一对一与群聊、用户消息与角色回复，文案与样式都只有这一份。
 *
 * 两种状态由 `state` 决定，语气不同是因为**后续可能不同**：
 *   - `pending`：还留在后端队列里，点「重试」真能补上，所以给按钮；
 *   - `failed`：重试过一次仍然写不进去，后端已经放弃了 —— 给按钮是骗人。
 *
 * 文案写死、不接受改文字 props：多个落点各写各的，早晚会出现「未保存」「没保存」
 * 「刷新会丢」三种说法，而用户要的是同一个判断。要改口径就改这里。
 */
export default function UnsavedHint({ state, onRetry }) {
  const pending = state === 'pending'
  return (
    <span className="unsaved-hint">
      <span className="unsaved-hint-text">
        {pending ? '未保存，刷新后会丢失' : '保存失败，刷新后会丢失'}
      </span>
      {pending && (
        <button type="button" className="unsaved-retry" onClick={onRetry}>
          重试
        </button>
      )}
    </span>
  )
}
