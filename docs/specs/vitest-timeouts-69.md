# Spec：vitest 全量套件在负载下偶发超时（issue #69）

基线：`b47faa4`（2026-09-30 的 main）。前端在 `6a5022c..b47faa4` 之间零改动，两个基线上的读数可以互换使用。

## 目标

- 负载下，全量 `npm test` 不再因为「机器慢」而偶发红。
- 「按时钟判断某件事没发生」的写法清零：改成按事件循环圈数、或按假时钟推进来判断。
- 两个等待上限只在一处定义，数值由实测推出，推导过程可以复算。

## 非目标

- 不升级 vitest 5（5.0.0 于 2026-09-03 发布，有 27 条破坏性变更），另行立项。
- 不把 jsdom 换成 happy-dom，不关 isolate（vitest 官方性能指南里的做法，属于性能优化，不在本次范围）。
- 不开 `retry`：它会把真卡死一起掩盖。
- 不接 eslint-plugin-testing-library：它的规则表里（v7.16.2）没有能拦住「先睡再断言」的规则；而推荐配置里的 `no-container` / `no-node-access` 会命中本仓大量现有用例。
- 其余 26 个文件里的 `waitFor` 不改。业界的模型本身就是「限时轮询」，上限跟着环境调即可。

## 已查实约束（每条都有坐标；S0 逐条复核，有一条不成立就停下报告）

| # | 事实 | 坐标 / 证据 |
|---|---|---|
| F1 | vitest 用例级超时默认 5000ms，浏览器模式下 15000ms；本仓原先没有配置 | `node_modules/vitest/dist/chunks/cac.D3xHeqeL.js:997`；v4.1.9 文档 `docs/config/testtimeout.md`；原 `vite.config.js` 的 `test` 块里没有 `testTimeout` |
| F2 | `waitFor` / `findBy` 每调用一次单独计时，默认 1000ms，每 50ms 轮询一次 | `@testing-library/dom/dist/config.js:15`、`wait-for.js:16,19`；dom 10.4.2 只改了类型定义，这些默认值未变 |
| F3 | `waitFor` 只在存在 `jest` 全局时才会自动推进假时钟；vitest 下没有这个全局。所以 **`waitFor` 不能和 vi 假时钟混用** | `@testing-library/dom/dist/helpers.js:17`；探针实测 `typeof jest === "undefined"` |
| F4 | React 调度器在 Node 下用 `setImmediate` | `node_modules/scheduler/cjs/scheduler.development.js:211` |
| F5 | `act()` 会一直把队列排空。被测代码如果陷入自触发循环，`act` 永远不返回，用例的超时计时器也没机会触发，变异就判不出结果 | 预跑实测：用 `act` 包住的排空写法，在 V2 变异下挂死，120 秒被掐掉 |
| F6 | 前端 vitest 是合并门 | `.github/workflows/build.yml:268` |
| F7 | 全仓 59 个测试文件：`waitFor(` 102 处（分布在 26 个文件）；`findBy` 5 处；**没有**任何用例依赖 `waitFor` 超时来通过（附近没有 `rejects` / `toThrow`） | `git ls-files` 全量 grep |
| F8 | 按时钟判断的写法共 4 处（全量扫描）：`DistillCancelError.test.jsx` 145、155 行各睡 80ms；`MinePagePresence.test.jsx:68` 睡 30ms；`usePresence.test.jsx:65` 用真实 40ms 定时器并限时 1000ms | grep `setTimeout\(` 与 `timeout:`，共 7 处命中，其余 3 处是 mock 里的 `setTimeout(0)` 或 store 测试的 `flush` |
| F9 | `DistillCancelError` 的 116 用例在「自触发循环」变异下**原本就会挂死**（原版 `settleMs(80)` 实测同样挂死），和缺陷 116 台账里记的「worker 被拖死」一致 | 预跑实测，见「补充」一节 |

## 已有机制清单（本次改动碰到的计时点）

| 机制 | 从什么时候开始计 | 依赖的前提 | 本次改动是否改变这个前提 |
|---|---|---|---|
| vitest `testTimeout` | 用例函数开始执行（不含 hook，hook 另有 `hookTimeout`） | 单条用例耗时远小于上限 | 上限调到 15000，前提重新成立 |
| RTL `asyncUtilTimeout` | 每次 `waitFor` / `findBy` 调用时 | 单次等待远小于上限 | 上限调到 5000 |
| RTL `asyncWrapper` 的收尾 | 每次 `waitFor` 结束后多排空一圈 `setTimeout(0)`（`@testing-library/react/dist/pure.js:89`） | 没有 | 不改。`flushTurns` 不依赖它 |
| SWR 的 `refreshInterval` | 每次请求完成后排一个 `setTimeout` | 真实时钟 | P6 改为 vi 假时钟手动推进，与负载无关 |
| 被测代码里的 `fetchWithTimeout` | 已被 mock，不计时 | — | — |

## 规模表（实测，原始读数都在本机 scratchpad，方法可复算）

方法：临时的 setup 文件包住 RTL 的 `asyncWrapper`，这是 `waitFor` 和 `findBy` 都必经的一个钩子。测量时把两个上限都放到 60s，记下每次等待和每条用例**实际需要**的毫秒数。测完即删，未入库。

| 负载 | 单次 `waitFor` 最大值 | 单次 p99 | 超过 1000 的次数 | 单条用例最大值 | 单条 p99 | 超过 5000 的次数 |
|---|---|---|---|---|---|---|
| 单套 | 235ms | 232ms | 0 / 131 | 654ms | 494ms | 0 / 293 |
| 8 套并发（4 核） | **2473ms**（StartChatReuse） | 2080ms | 41 / 1048 | **6338ms**（StartChatReuse） | 3659ms | 7 / 2344 |

真实规模的对照：issue 附的 Windows 日志里整套 `tests` 累计 84.13s（`docs/specs/artifacts/hide-evidence-rail/be5c8414-full-run2.txt`），本机 8 倍负载下是 94.3s。也就是说，那台机器日常就相当于这里的 8 倍负载。

**取值** = 8 倍负载下的实测最大值 × 2，向上取整：
- `ASYNC_UTIL_TIMEOUT_MS = 5000`（2473 × 2 = 4946）
- `TEST_TIMEOUT_MS = 15000`（6338 × 2 = 12676）

「× 2」这个余量系数是判断，不是标准，已经你确认。

## 出处对照（机制是照抄的，数值是按上面实测推出的）

| 出处 | 条目 | 本 spec 对应 |
|---|---|---|
| Testing Library `api-configuration` | `asyncUtilTimeout` 可配置，默认 1000 | C1 |
| Grafana `public/test/setupTests.ts` | CI 并行下 `configure({ asyncUtilTimeout: 2000 })` | C1（同一机制；数值按本仓实测取） |
| vitest `config/testtimeout` | 为 DOM 渲染场景提高 `testTimeout`（浏览器模式 15000） | C1 |
| Testing Library `guide-disappearance` | 断言「不存在」用 `queryBy` / `not.toBeInTheDocument`，不靠睡眠 | C2、C3 |
| Testing Library（F3） | 用假时钟时不能依赖 `waitFor` 自动推进 | C4：用 `act` + `vi.advanceTimersByTimeAsync` |

## 改动

- **C1 超时上限只在一处定义**
  - 新增 `src/test/timeouts.js`，导出两个常量。
  - `vite.config.js` 的 `test.testTimeout` 读 `TEST_TIMEOUT_MS`。
  - `src/test/setup.js` 调 `configure({ asyncUtilTimeout })`，读 `ASYNC_UTIL_TIMEOUT_MS`。
  - 新增 `src/test/timeouts.test.js`，锁住接线：setup 那一行被删掉时，这是唯一会红的地方。
- **C2 可复用的收尾辅助**：新增 `src/test/flushTurns.js`，按事件循环圈数推进（默认 2 圈），**不包 `act`**（原因见 F5）。
  - 实测最少 0 圈就够判定：前面那次 `waitFor` 内部自带一圈排空。
  - 但那是库的实现细节，所以显式补 2 圈作为余量。这个数是判断。
- **C3 两处睡眠型断言改用 C2**：`MinePagePresence` 的 M2；`DistillCancelError` 的两条 116 用例。
  - 116 这两条另加 `answerTextListOnce()`：文本列表接口只应答第一次请求，之后的请求一律挂起。自触发循环因此停在第 2 次请求上，变异可以判定（修 F9）。
  - 原来的 `settleMs` 及其注释一并删除。
- **C4 `usePresence` 的 P6** 改用 vi 假时钟：`advanceTimersByTimeAsync(0)` 后请求次数为 1，再推进两次 40ms 后恰好为 3。不再使用 `waitFor`，原因见 F3。

## 调用点 × 可观测输出矩阵

| 调用点 | 可观测输出 | 守它的测试 |
|---|---|---|
| `setup.js` 的 `configure` | `getConfig().asyncUtilTimeout === 5000` | `src/test/timeouts.test.js` |
| `vite.config.js` 的 `testTimeout` | 8 倍负载下用例不超过 15s 就不判超时 | 验收：8 套并发 8/8 绿 |
| `MinePage` 的在线状态（对方隐藏） | 不渲染 `.mine-online` | `MinePagePresence` M2 |
| `DistillWorkbench` 挂载 | `/api/text/list` 只请求 1 次 | `DistillCancelError` 「蒸馏工作台：/api/text/list 只拉一次」 |
| `TextPanel` 的角色管理 | `/api/text/list` 只请求 1 次 | `DistillCancelError` 「创作页角色管理：/api/text/list 只拉一次」 |
| `usePresence` 的 `refreshInterval` | 按间隔重新查询：推进 0 → 1 次，再推进 2×40ms → 3 次 | `usePresence` P6 |

## 变异（发出前已在当前代码上预跑，全部打红、没有挂死）

| 编号 | 变异 | 结果 | 红在哪条断言 |
|---|---|---|---|
| V1 | `MinePage`：对方隐藏时仍显示状态（`presence.online !== null \|\| presence.hidden`） | RED | `expected <span class="mine-online off">… to be null` |
| V2 | `DistillWorkbench`：卡片 effect 里自触发 `loadTexts`（恢复 116 的形态） | RED | `expected 3 to be 1` |
| V3 | `TextPanel` 角色管理：同上（在内层组件补上 `loadTexts` 的取值，忠实复刻原形态） | RED | `expected 3 to be 1` |
| V4 | `usePresence`：不传 `refreshInterval` | RED | `expected 1 to be 3` |
| V5 | `setup.js`：删掉 `configure` 那一行 | RED | `expected 1000 to be 5000` |
| V6 | `usePresence`：间隔被放大 1000 倍 | RED | `expected 1 to be 3` |

## 测试

- 本地只跑受影响的文件：`npx vitest run src/test src/components/__tests__/MinePagePresence.test.jsx src/components/__tests__/DistillCancelError.test.jsx src/hooks/__tests__/usePresence.test.jsx`，再跑一次 `npm test`。
- CI 用的 lint：`npx eslint . -c eslint.ci.config.js --quiet`。
- 验收：8 套 `npx vitest run` 并发跑。基线 8/8 红，改后 8/8 绿。**实测已达成**：改后 8/8 绿，294 passed，最慢的 StartChatReuse 为 5.0–5.4s，在旧上限下本会红。
- 合并门以分支 CI 为准；合并只做 git 操作。

## 补充（执行中的发现，已在本段改动面内处理）

1. **`act` 包裹的排空会让变异挂死（F5）。** 第一版 `flushAsync` 用了 `act`，V2 实测挂死。改为不包 `act`、按圈数推进。
2. **V3 第一版变异写错了。** 它红，是因为内层组件里 `loadTexts is not defined`，不是被测行为触发的，属于红错了地方。已按缺陷 116 的原形态重写，并确认红在请求次数那条断言上。
3. **F9 是原有问题**：116 用例在自触发循环变异下本来就挂死，原版 `settleMs` 也一样。由 `answerTextListOnce` 修复。这属于本段改动面，直接修了，没有另记账。
4. 第一次 CI lint 就拦下了 `timeouts.test.js` 少导入 `it` / `expect`（no-undef）。已按本仓惯例显式 `import from 'vitest'`。
