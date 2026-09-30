// 测试等待上限的唯一出处（vite.config.js 与 src/test/setup.js 都从这里读）。
// 取值 = 负载实测最大值 × 2，推导与原始读数见 docs/specs/vitest-timeouts-69.md。
export const TEST_TIMEOUT_MS = 15_000
export const ASYNC_UTIL_TIMEOUT_MS = 5_000
