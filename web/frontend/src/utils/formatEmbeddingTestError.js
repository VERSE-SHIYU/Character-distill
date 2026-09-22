/**
 * 把 `/api/auth/test-embedding` 的失败体拼成给用户看的文案。
 *
 * 这是**排障**按钮：后端的中文提示（`error`）在前，上游原话（`detail`）在后，
 * 用换行分开，由调用方把小号那行渲染出来。缺 `detail` 时只有提示，不留空行。
 */
export function formatEmbeddingTestError(data) {
  const hint = (data && data.error) || '未知错误'
  const detail = data && data.detail
  return detail ? `${hint}\n详细信息：${detail}` : hint
}
