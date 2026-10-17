# spec：「导出角色卡」接回已有的下载函数，直接下载 JSON

基线：main `461e81d`。放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
依据：Shiyu 10-17：卡片详情页点「导出角色卡」弹出一个页面；五月时是直接下载 JSON 文件。要求：不重写，把已有的接回去。

## 解决什么、不解决什么

| 解决 | 不解决 |
|---|---|
| 卡片详情页「导出角色卡」直接下载 `<角色名>.json` | `DistillWorkbench.jsx:57` 另有一份本地下载函数（设了 `a.download`，能用）：重复，但不在目标内（第 37 条），记进调查报告 |
| | 后端两个返回同一份原始 JSON 的接口（`web/routers/card.py:68`、`web/routers/distill.py:1449` `?format=raw`）：同上，只记账 |

一个 PR，只改前端一个组件，加一条测试。

## 根因（已查实，完整 git 历史）

| # | 判断 | 坐标与原文 |
|---|---|---|
| J1 | 5-20 时按钮能直接下载：设了下载文件名 | `167be57a`（2026-05-20「角色卡导出改为fetch下载」）`CharCard.jsx`：`a.href = url` 后有 `a.download = \`${card.name}.json\`` |
| J2 | **5-23 换后端接口时把这一行删了**，之后一直没加回来 | `393e09a2`（2026-05-23「add card export endpoint and update export button」）：`-  a.download = \`${card.name}.json\``，同时 `?format=tavern` 改成 `/api/cards/${card.id}/export` |
| J3 | 现在的按钮：没有 `a.download` → 浏览器把 blob 链接当成普通链接打开 = 你看到的弹出页面 | main `CharCard.jsx:921-940` |
| J4 | 仓库里**已经有**一个正确的下载函数，「我的」页在用 | `web/frontend/src/api/client.js:429` `exportCard(cardId, format = 'raw')`：请求 `/api/distill/cards/${cardId}/export?format=raw`，从 `Content-Disposition` 取文件名，设 `a.download`；`MinePage.jsx:92` `exportCard(card.id, 'raw')` |
| J5 | 它调的后端接口返回的就是卡片原始 JSON，带下载头 | `web/routers/distill.py:1484-1493`（`format == "raw"` → `record["card_json"]`，`Content-Disposition: attachment; filename*=UTF-8''<名>.json`） |

## 改法（接回去，不重写）

1. `CharCard.jsx` 的「导出角色卡」按钮：删掉内联那段，改调已有的 `exportCard(card.id)`（从 `../api/client` 导入）。失败时照该文件现有的错误提示写法显示「导出失败：…」。
2. 不改 `client.js`、不改后端、不动其他组件。

## 结构自检

| 问题 | 答案 |
|---|---|
| 「下载卡片」改后写了几处 | `client.js` 一处，「我的」页与详情页共用；`DistillWorkbench` 的本地那份照旧（记账，见上表） |
| 详情页还要知道下载细节吗 | 不要，只传 card id |

## 目标检查（vitest，第 1 个提交就带上）

1. 卡片详情页点「导出角色卡」→ 调到 `exportCard(card.id)` 一次（mock `../api/client`）。main 上红。
2. `exportCard` 抛错 → 页面显示错误提示。main 上红。

## 测试

- 本地只跑新测试，加上 `grep -rln "card-export-btn\|导出角色卡" web/frontend/src` 搜出来的测试文件（目前是 `GuestWriteEntries.test.jsx`；以实际输出为准，贴原始输出）。后端没改，不跑 pytest。
- **CI 绿了才报「可合并」**。
- 部署后 Shiyu 手测：详情页点「导出角色卡」，直接下载到 `<角色名>.json`。

## 对账表（独立复核方验）

| 行为 | 守它的测试 | 让它变红的变异 |
|---|---|---|
| 详情页走已有下载函数 | 1 | 按钮里恢复内联写法 |
| 出错有提示 | 2 | 吞掉错误 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按判断清单核对，跑 grep |
| 改动 | `@tdd` | 先写目标检查再改 |
| 交付 | `@verification-before-completion` | 贴测试原始输出与 CI 结果 |

## 合并前补修（2026-10-17，debug R1）

- **R1 回归**：旧按钮走 `fetchWithTimeout`，401 会刷新 token 再重试；`exportCard` 用的是裸
  `fetch`，access token（30 分钟）过期后导出直接失败，而页面其他按钮都能静默续期。
- **改法**：`client.js` 的 `exportCard` 改用 `fetchWithTimeout`（它已在 `!ok` 时抛带 detail 的
  `AppError`，手写 ok 判断删除）；blob URL 改为延迟 1 秒释放（与 `DistillWorkbench` 一致）。
  「我的」页与详情页共用这一处，两个页面同时修好。
- **目标检查**：`src/api/__tests__/exportCard.test.js` —— 401 → 刷新 → 重试成功 → 触发下载。
  裸 fetch 时红（fetch 只调 1 次），改用 `fetchWithTimeout` 后绿。
- **变异**：把 `fetchWithTimeout` 改回裸 `fetch` → 该检查变红。

## 已知风险（本 PR 不处理）

- **R2**：旧接口 `web/routers/card.py:68` 对 `card_json` 解析失败有容错；新接口
  `distill.py` 先跑 `CharacterCard.model_validate_json`，旧格式/不合规卡片会 500。
  「我的」页早已暴露，线上未发现不合规卡片，记录不处理。

## 交付

一个 PR，英文标题、描述、commit。不写部署步骤。
