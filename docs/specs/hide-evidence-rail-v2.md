# Spec v2：聊天界面不再渲染「检索来源」

基线 `origin/main`；分支 `fix/hide-evidence-rail`；worktree `.claude/worktrees/hide-evidence-rail`。
**只改前端，且只删不增**。后端 evidence 帧、落库列、history 透传一帧不动。
取代 v1（`hide-evidence-rail.md`）：v1 走 e2e，依赖真后端 + 口令 + 种子数据，样本无效已作废。

## 目标

聊天界面（实时流回复、历史/重逢加载两条路径）不再出现「检索来源」行，相关前端代码整体移除。
后端照旧发 evidence 帧，前端收到后**静默忽略**（不报错）。

## 非目标

- 不动 `web/routers/chat.py` 的 evidence 帧、`web/routers/history.py` 的透传、messages 表 evidence 列。
- 不新增开关 / 偏好项 / 依赖 / feature flag；不留半成品。
- 不动 `.evidence-*` 之外的任何代码，不动相邻共享 token（`fade-up` / `--ease-out` / `--input-border` / `--accent-rgb`）。
- 不做 e2e：不起后端、不需要口令、不写任何库。

## 已查实约束（本 worktree 实测，命令与输出均为原始）

路径存在性（先确认再扫描）：

```
$ for p in src/components/ChatArea.jsx src/store/useAppStore.js src/api/client.js \
    src/styles/global.css src/components/common/EvidenceRail.jsx \
    src/components/__tests__/EvidenceRail.test.jsx \
    src/components/__tests__/ChatAreaMemoryError.test.jsx; do [ -f "$p" ] && echo "EXISTS $p"; done
EXISTS  src/components/ChatArea.jsx
EXISTS  src/store/useAppStore.js
EXISTS  src/api/client.js
EXISTS  src/styles/global.css
EXISTS  src/components/common/EvidenceRail.jsx
EXISTS  src/components/__tests__/EvidenceRail.test.jsx
EXISTS  src/components/__tests__/ChatAreaMemoryError.test.jsx
```

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 渲染点唯一一处 + 唯一 import | `components/ChatArea.jsx:1066`（渲染）、`:12`（import） |
| C2 | `evidence` 的全部读取方共 3 处，同一条链（`msg.evidence` → prop → rail） | `ChatArea.jsx:726`（传参）、`:993`（形参）、`:1066`（渲染）；删渲染后存侧写入无读取方 |
| C3 | 实时流写入：`streamSSE` 第 7 实参 `onEvent` 只认 evidence | `store/useAppStore.js:1504`（调用）、`:1523-1535`（实参，`:1526` `if (payload.type !== 'evidence') return`） |
| C4 | 历史/重逢写入：`resumeSession` 映射 | `store/useAppStore.js:1689`（`evidence: m.evidence ?? null`） |
| C5 | 未知帧静默：分发是**无 else 的 if 链**，删掉第 7 实参后 evidence 帧落空不抛 | `api/client.js:262-269`（`:264` `else if (payload.type && onEvent)`、`:270` `catch { /* skip malformed lines */ }`） |
| C6 | 「不传 onEvent」本仓已有先例：第二处 `streamSSE` 只传 5 个实参 | `useAppStore.js:1626-1641` |
| C7 | 群聊那处 `onEvent` 只认 user/reply，不碰 evidence，且是另一条链 | `GroupChatPage.jsx:689`、`:701-724` |
| C8 | 组件与测试各一个文件 | `components/common/EvidenceRail.jsx`、`components/__tests__/EvidenceRail.test.jsx` |
| C9 | 样式是一整块连续区间，13 个类**逐个 grep 均无其他复用方** | `styles/global.css:10103-10187` |

C5 原文（`api/client.js`）：

```js
            if (payload.token !== undefined) {
              onToken(payload.token)
            } else if (payload.type && onEvent) {
              onEvent(payload)
            }
            if (payload.status !== undefined && onStatus) {
              onStatus(payload)
            }
          } catch { /* skip malformed lines */ }
```

C3 原文（`api/client.js:186` 的签名 7 参 → `useAppStore.js:1504` 第 7 个实参）：

```js
// client.js:186
export function streamSSE(url, body, onToken, onDone, onError, onStatus, onEvent) {

// useAppStore.js:1522-1535（第 6 实参 onStatus 为 undefined，第 7 即 onEvent）
      undefined,
      // evidence 帧先于 token 流到达（后端在首个 token 前发），此刻末条仍是本轮 char 占位。
      // 认 type 不认字段存在性：未知 type 一律忽略（后端将来加事件不该让前端出意外）。
      (payload) => {
        if (payload.type !== 'evidence') return
        ...
      },
```

→ `onEvent` 在 store 侧**只处理 evidence**（`:1526` 非 evidence 直接 return）。删掉该实参后，
`client.js:264` 的 `&& onEvent` 为假 → 整个分支跳过 → evidence 帧既不入 store 也不报错（C5、C6）。

C9 逐类复用检查（在 `web/frontend` 内排除 `node_modules` 与 `global.css` 后逐类 `rg`）：
13 个类 `.evidence-rail / -toggle / -arrow / -arrow-open / -body / -card / -card-head /
-card-status / -item / -item-text / -item-truncated / -item-link` **只命中
`EvidenceRail.jsx` 与其测试**，零复用。（`.evidence-summary` 仅 JSX 有、`global.css` 无规则，见补充 2。）

## 改动（步骤 1~5，每步独立 commit）

1. **[测试-先红]** 新增 `src/components/__tests__/ChatAreaNoEvidence.test.jsx`：仿
   `ChatAreaMemoryError.test.jsx` 的 mock store 写法，messages 放一条带 evidence 的 char 消息，
   断言渲染结果中不存在检索来源节点。当前代码下必须失败（见「变异」）。
2. **[渲染]** `ChatArea.jsx` 删 `:12` import 与 `:1066` 渲染点（`:726` 传参、`:993` 形参同属本条）。
3. **[store]** `useAppStore.js` 删 `:1504` 那次 `streamSSE` 的第 7 实参（`:1523-1535`），
   删 `:1689` 的 `evidence` 映射。依据 C2：删渲染后这些写入无任何读取方。
4. **[样式]** `global.css` 删 `:10103-10187` 整块（C9 已证无复用）。
5. **[组件+测试]** 删 `EvidenceRail.jsx` 与 `EvidenceRail.test.jsx`。

## 调用点矩阵

| 调用点 | 可观测输出 | 守护 |
|---|---|---|
| ChatArea 渲染 char 消息 | 无检索来源节点 | `ChatAreaNoEvidence.test.jsx` |
| 流式收到 evidence 帧 | 不报错、不写入状态 | `api/client.js:262-269` 的现有分发条件（C5，步骤 0 贴出原文） |

## 变异（实施前先做，有存活即停）

| 变异 | 应红于 |
|---|---|
| 把渲染行 `{!isUser && <EvidenceRail evidence={evidence} />}` 加回 `ChatArea.jsx` | `ChatAreaNoEvidence.test.jsx`（须重新 import，否则编译即红——两种红都记） |
| 把 `onEvent` 实参加回 `useAppStore.js` | 无组件层红源；由 C5/C6 的代码事实担保，非测试担保 |

第二条无 vitest 红源，如实记录，不假装有测试守着。

## 判据

1. `rg -n "EvidenceRail" web/frontend/src` → 命中**只剩**
   `src/components/__tests__/ChatAreaNoEvidence.test.jsx`（该测试点名断言选择器不存在，属正当用法），
   其余（含 `ChatArea.jsx`、`EvidenceRail.jsx`、`global.css`）零命中。
2. `npm test` 全绿。
3. `git diff --stat` 只含本任务文件。

> 不改用 `[class*="evidence"]` 模糊选择器：那是为迁就判据而降低断言精度，且可能误匹配。

## 补充（意外发现 / 与 v1 的偏离）

1. **验证方式整体改道（用户裁决）**：v1 的 e2e 验收脚本 + `helpers.cjs` 的 `E2E_BASE` 覆盖已撤回/删除，
   改为 vitest 组件层。原因：e2e 需要真后端 + 口令 + 种子数据三样同时成立，而借用的 7861 库对
   testadmin 无匹配卡片（`[distill] list_cards text_id=cd124e88e923 user_id=... => 0 cards`），
   样本无效 → 先红都跑不出来。组件层不起后端、不需口令、不写库，代价是测不到 SSE 真帧。
2. **`.evidence-summary` 只有 DOM 类、没有 CSS 规则**：JSX 用了它（`EvidenceRail.jsx:101`），
   `global.css` 里无对应选择器。删除时无需处理，也不属于「专属样式」清单。
3. **第二处 `streamSSE`（hidden / 开场白，`useAppStore.js:1626`）从来不传 `onEvent`** →
   那条路径上的 evidence 帧**一直**是静默丢弃的。即「删掉 onEvent」不是新范式，是既有范式。
4. **e2e 的 `helpers.cjs` 已恢复原状**（`git checkout`），`e2e/evidence-absence-verify.cjs` 已删；
   5174 上的两个 vite dev 进程（PID 63212 / 50132）已 `taskkill`。
5. **`ChatAreaNoEvidence.test.jsx` 不 mock `ChatBubble`**：现有 `ChatAreaMemoryError.test.jsx` 把
   `ChatBubble` mock 成 `null`，若照抄则整条消息都不渲染 → 「没有 rail」会**空过**。本测试改为
   渲染真实 `ChatBubble`（它只把 children 原样渲染），并额外断言「消息正文真的在」。
6. **本机全量套件有既有的负载性超时 —— 与本次改动无关，另立议题（不属本 spec 范围）**。
   实施期间步骤 3、步骤 4 的全量跑各出现一次超时，故做了对照实验。

   **基线 `be5c8414`（未改动）整套跑 3 次 → 3 次全红，且每次红的文件不同：**

   | 基线 run | 红文件 | 性质 |
   |---|---|---|
   | 1 | `GroupErrorKeepsPending`、`GroupRetrySendsPendingKeys` | `Test timed out in 5000ms` |
   | 2 | `HistoryPanelFilter` | `Test timed out in 5000ms` |
   | 3 | `GroupRetrySendsPendingKeys`、`HistoryPanelFilter` | `Test timed out in 5000ms` |

   原始日志：`docs/specs/artifacts/hide-evidence-rail/be5c8414-full-run{1,2,3}.txt`。

   **单跑对照（在各自 commit 上）**：
   - `bebee7c7`（步骤 3）跑 `HistoryPanelFilter` × 3 → 3/3 绿（4.02s / 4.26s / 3.97s）
   - `e726ef41`（步骤 4）跑 `AdminPanelRoleSelect` × 3 → 3/3 绿（5.97s / 7.11s / 9.06s）

   **与 evidence 无关**：`rg -ni evidence` 在两个文件内零命中；二者只 import vitest / RTL / 各自的组件。

   → 超时是既有的环境抖动（负载下 5000ms 默认超时不够），**非本次改动引入**，判据是「基线也红」。
   **另立议题**：整套用例的默认超时 / prerender 成本需单独处理，不塞进本 spec。
