> **已取代，见 `hide-evidence-rail-v2.md`**（2026-09-29）。
> 本文件的 e2e 验收路线作废：验收脚本依赖「真后端 + 口令 + 种子数据」三样，
> 而借用的 7861 库对 testadmin 无匹配卡片（`list_cards ... => 0 cards`），样本无效。
> 判据、调用点矩阵、验证方式均已由 v2 取代。保留本文件仅为留痕。

# Spec：聊天界面不再渲染「检索来源」

基线 `origin/main` `be5c8414`；分支 `fix/hide-evidence-rail`；worktree `.claude/worktrees/hide-evidence-rail`。
**只改前端**。后端 evidence 帧、落库列、history 透传一帧不动。

## 目标

聊天界面（实时流回复、历史/重逢加载两条路径）不再出现「检索来源」行，与之相关的前端代码整体移除。
后端照旧发 evidence 帧，前端收到后**静默忽略**（不报错）。

## 非目标

- 不动 `web/routers/chat.py` 的 evidence 帧、`web/routers/history.py` 的 evidence 透传、messages 表 evidence 列。
- 不新增开关 / 偏好项 / 依赖 / feature flag；不留「以后可能还要」的半成品。
- 不清理 `.evidence-*` 之外的任何代码，不动相邻的 `fade-up` / `--ease-out` / `--input-border` 等共享 token。

## 已查实约束（`be5c8414`，S0 逐条复核；任一不成立即停）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 渲染点唯一一处 + 唯一 import | `components/ChatArea.jsx:1066`、`:12` |
| C2 | `evidence` 的全部读取方共 3 处，且都在同一条链上（`msg.evidence` → prop → rail） | `ChatArea.jsx:726`（传参）、`:993`（形参）、`:1066`（渲染） |
| C3 | 实时流写入：`streamSSE` 第 7 参 `onEvent` 里挂 evidence | `store/useAppStore.js:1525-1535` |
| C4 | 历史/重逢写入：`resumeSession` 映射 `evidence: m.evidence ?? null` | `store/useAppStore.js:1689` |
| C5 | 组件与测试各一个文件 | `components/common/EvidenceRail.jsx`、`components/__tests__/EvidenceRail.test.jsx` |
| C6 | 样式是一整块连续区间，13 个类**逐个 grep 均无其他复用方** | `styles/global.css:10103-10187` |
| C7 | 未知帧静默：派发是**无 else 的 if 链**，删掉第 7 参后 evidence 帧落空不抛；另有 catch 兜底 | `api/client.js:264`（`else if (payload.type && onEvent)`）、`:270`（`catch { /* skip malformed lines */ }`） |
| C8 | 「不传 onEvent」在本仓已有先例：第二处 `streamSSE` 从来不传；群聊 `onEvent` 只认 user/reply、无 else | `useAppStore.js:1626-1641`、`GroupChatPage.jsx:701-724` |
| C9 | 除 C1–C6 外无任何其他引用：`web/frontend` 排除 `src/` 后 `grep -i evidence` 零命中；`e2e/` 零命中 | — |

## 改动（4 个 commit，每步独立）

1. **前端-渲染**：`ChatArea.jsx` 删 `:12` 的 import 与 `:1066` 的渲染点（`MessageBubble` 的 `evidence` 形参 `:993` 与传参 `:726` 同属本条）。
2. **前端-store**：`useAppStore.js` 删 `:1525-1535` 的 `onEvent` 实参、删 `:1689` 的 `evidence` 映射。依据 C2+C9：删掉渲染后 C4 的写入无任何读取方。
3. **样式**：`global.css` 删 `:10103-10187` 整块（C6 已证明无复用）。
4. **组件+测试**：删 `EvidenceRail.jsx` 与 `EvidenceRail.test.jsx`。

## 判据（用户裁决，两条都查）

1. `rg -n "EvidenceRail|evidence-rail" web/frontend/src` → **输出为空**。
2. `rg -l "EvidenceRail|evidence-rail" web/frontend` → 命中**只剩验收脚本本身**，多一个文件都不行。

> 验收脚本位于 `web/frontend/e2e/`（**在 `src/` 之外**，已确认），故判据 1 不受影响。
> 脚本用精确选择器 `.evidence-rail` 断言数量为 0 —— 回归测试点名「它不存在」属正当用法。

## 调用点矩阵

| 调用点 | 可观测输出 | 测试 |
|---|---|---|
| 实时流回复 | 不渲染检索来源行 | 真发一条消息 → 断言 `.evidence-rail` 等节点数为 0 |
| 实时流回复 | 收到 evidence 帧不报错、不落进 store | 同一条流：断言本轮 evidence 帧 ≥1，且 store 末条消息**没有** `evidence` 键；断言 pageerror / console error 为空 |
| 历史消息加载 | 不渲染检索来源行 | 刷新页面走 resume → 断言节点数为 0 |

## 验收脚本

`web/frontend/e2e/evidence-absence-verify.cjs`，复用 `e2e/helpers.cjs`（`openApp` / `login` / `seedChat` / `shot`）。
**断言顺序（防「空过」，不许先断言 0 再补前置）**：

- a. 先断言本轮 SSE 里 evidence 帧数 **≥1 且 `evidence` 非空** —— 不满足则直接判失败并打印「样本无 evidence，结果无效」（`exit 1`）。这一条是「数量为 0」有意义的前提。
- b. 断言 `.evidence-rail` / `.evidence-toggle` / `.evidence-card` / `.evidence-item` 计数均为 **0**。
- c. 断言 `window.__appStore` 末条消息无 `evidence` 键。
- d. 断言 `pageerror` 与 console error 为空。
- e. 刷新后（resume 路径）重跑 b，并截图。

## 变异（实施前先做，有存活即停）

先贴**未变异态绿**的原始输出，再逐个注入：

| 变异 | 应红于 |
|---|---|
| 把渲染行 `{!isUser && <EvidenceRail evidence={evidence} />}` 加回 `ChatArea.jsx` | b（且需重新 import，否则编译即红——两种红都记） |
| 把 `onEvent` 实参加回 `store/useAppStore.js` | c |
| 把 `evidence: m.evidence ?? null` 加回 `resumeSession` | 仅当 d 的 resume 路由能返回真 evidence 才有分辨力 |

任一变异存活 → 停下报告，不继续删。

## 运行环境（已查实 + 阻塞项）

**已查实**

- `.env` 在 `.gitignore:151`；本 worktree 内**不存在**，主仓与 `.claude/worktrees/split-list` 各有一份。
- `docker-compose.local.yml` 的 app 端口硬编码 `7861:7860`；该文件**已入库**（`git ls-files` 命中），但文件头自称「不要提交 git」——只记录，本次不动它。
- 镜像内含前端构建产物（`Dockerfile:4-9` 的 `npm run build`、`:34` 拷入 `dist`）→ 走容器就等于测镜像里的 build，**测不到本 worktree 的源码改动**；要测本线源码须 `vite dev` 或重新构建镜像。
- 端口现状：`7861` 被另一条线 `split-list-app-1` 占用，`5173` 被占用，`7860` / `7864` 空闲。

**已裁决（用户）**

1. **后端**：借已在跑的 `7861` 那条栈（testadmin / 蒸馏文本 / 角色卡都在那儿），**本 worktree 不另起栈**（7861 与 5432 均被 `split-list` 线占用，本 worktree 无 `.env` 且 `data/` 为空目录 —— 自起栈等于全新空库，要先播种）。代价已知并接受：会把一条测试消息写进那条线的库（既有 e2e 脚本同样是这个行为）。
2. **页面地址**：本线源码走 `vite dev`（默认 5173 被占，用 5174），代理指向 7861 —— 否则测的是镜像里的 build，测不到本次改动。为此 `e2e/helpers.cjs` 的 `BASE` 加一行 `process.env.E2E_BASE ||` 覆盖（超出本任务点名面，已先问后改）。
3. **凭据**：`TEST_PASSWORD` 由用户 `!` 执行脚本时注入，不入仓、不经我手。

**待实测（脚本第 a 条断言即探针，不会静默空过）**

- testadmin 能否真跑通一轮 LLM：`e2e/chat-dualpane-verify.cjs:5` 原注释称「testadmin 无 LLM API Key，原版 resume 也会 503」。若本地无处兜底全局 key，则拿不到非空 evidence 帧 → 脚本判「样本无 evidence，结果无效」并 `exit 1`。

**运行命令（骨架）**

```
# 1) 在 web/frontend 起本线前端（后台）
VITE_PROXY_TARGET=http://localhost:7861 npm run dev -- --port 5174

# 2) 跑验收（口令由人注入）
E2E_BASE=http://localhost:5174 TEST_PASSWORD=*** node e2e/evidence-absence-verify.cjs
```

## 补充（意外发现 / 本 spec 的偏离）

1. **判据收窄（用户裁决）**：原判据「`rg "EvidenceRail|evidence-rail" web/frontend` 为空」会与验收脚本冲突——脚本必须点名断言该选择器不存在。故改为上面两条。**不**采用 `[class*="evidence"]` 模糊选择器：那是为迁就判据而降低断言精度，且可能误匹配。
2. **`.evidence-summary` 只有 DOM 类、没有 CSS 规则**：JSX 用了它（`EvidenceRail.jsx:101`），`global.css` 里无对应选择器（实测 `grep -rn evidence-summary web/frontend` 只命中 jsx 与测试）。删除时无需处理，也不属于「专属样式」清单。
3. **第二处 `streamSSE`（hidden / 开场白，`useAppStore.js:1626`）从来不传 `onEvent`** → 那条路径上的 evidence 帧**一直**是静默丢弃的。即「删掉 onEvent」不是新范式，是既有范式。
4. **`docker-compose.local.yml` 已入库**，与其文件头「不要提交 git」自相矛盾（见上）。
5. **本 worktree 的 `web/frontend` 与 `origin/main` 逐字节相同**（`git diff --stat HEAD origin/main -- web/frontend` 为空），故 S0 结论可直接用于本分支。
