/**
 * 按条目的 `requires` 字段和当前 flag 过滤入口列表。`EntryList` 与 `EntryGrid` 共用这一份。
 *
 * 条目上写 `requires: 'isAdmin'` / `requires: 'canWrite'`，调用处传
 * `flags={{ isAdmin: …, canWrite: … }}`。**flag 缺失按不可见处理**：
 * 新开一个 flag 却忘了在某个调用处传，结果是该入口藏起来（看得见的问题），
 * 而不是对所有人放行（看不见的问题）。
 */
export function visibleEntries(entries, flags = {}) {
  return entries.filter((entry) => !entry.requires || flags[entry.requires])
}
