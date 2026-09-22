/**
 * 把 `/api/auth/test-embedding` 的失败体拆成两段，交给调用方各渲染各的。
 *
 * 返回对象而不是拼好的字符串：中文提示（`hint`）与原话（`detail`）在页面上字号、
 * 颜色都不同，拼成带 `\n` 的字符串再拆开，等于把「怎么显示」的约定藏进一个换行符里。
 *
 * `hint` 缺 `error` 时兜「未知错误」；`detail` 没有时给空串（调用方按 falsy 决定渲不渲染）。
 * `detail` 原样返回 —— 上游原话可能多行，不许截断（这是排障按钮，截断就等于丢线索）。
 */
export function formatEmbeddingTestError(data) {
  return {
    hint: (data && data.error) || '未知错误',
    detail: (data && data.detail) || '',
  }
}
