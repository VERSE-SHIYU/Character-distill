# 管理后台「对端」行不给写入口（2026-09-29，Shiyu 已定：先修这个）

> 放到 worktree 的 `docs/specs/admin-peer-rows.md`，以后的补充写进本文件。
> **执行方**：distill-mainline 窗口，从 origin/main（基线 `be4ad239`）新开分支 `fix/admin-peer-rows`（`--unset-upstream`）。
> 只改前端；跨节点禁用/启用是下一份 spec，不在本段。

## 目标
联邦用户列表里的「对端」行不再出现任何写入口，操作不会打到本节点同 id 的账号上。

## 问题（代码现读，基线 `be4ad239`）
- 用户管理页的写接口全是**本节点**接口，且只带 `userId`（`web/frontend/src/api/client.js:301-386`）。
- 列表把本地与对端拼成一个数组（`AdminPanel.jsx:332`），但对端行只禁用了「禁用/启用」和角色下拉；「详情」「重置密码」「删除」和批量勾选框照常可点。
- offerPass 在 SG、SZ 是**同一个 id**（整号复制）。点对端那行的删除 / 重置密码，打到的是**本节点**同 id 的账号；同 id 两行还共用 React key（`key={u.id}`）。
- 对端下发的是隐私政策第 (5) 条白名单字段，**不含角色**（`storage/postgres_store.py:2404`），对端行现在显示的角色是空或错的。

## 出处对照表
| 规范条目 | 出处 | 本 spec 行为 |
|---|---|---|
| 同级元素的 key 必须唯一，否则 React 会混淆行 | react.dev「Rendering Lists → Rules of keys」 | 行为 2 |
| 对端字段白名单不含 `role` | `web/frontend/src/legal/privacy_v3.md` 第 (5) 条；`postgres_store.py:2404` 的 SELECT | 行为 4 |
| 本页写接口只作用于本节点用户 | `web/routers/admin.py`（disable/enable/role/reset-password/delete/batch-delete/email/detail 均按本库 `user_id`） | 行为 1、3 |

## 全量扫描（`git show origin/main:web/frontend/src/components/AdminPanel.jsx`，按用户 id 发请求或判对端的全部位置，原始输出）
```
350:        await adminAPI.enableUser(user.id)
352:        await adminAPI.disableUser(user.id)
365:      await adminAPI.setUserRole(user.id, role)
386:      await adminAPI.resetPassword(resetTarget.id, newPassword)
405:      await adminAPI.setUserEmail(emailTarget.id, newEmail)
424:      await adminAPI.deleteUser(deleteTarget.id)
445:    const selectable = users.filter((u) => u.id !== authUser?.id && !isAdmin(u))
447:    const allSelected = selectable.every((u) => selectedUsers.has(u.id))
459:      const res = await adminAPI.batchDeleteUsers([...selectedUsers])
517:                    checked={users.filter((u) => u.id !== authUser?.id && !isAdmin(u)).length > 0 &&
518:                      users.filter((u) => u.id !== authUser?.id && !isAdmin(u)).every((u) => selectedUsers.has(u.id))}
533:                <tr key={u.id}>
535:                    {u.id === authUser?.id ? null : isAdmin(u) ? (
538:                      <input type="checkbox" checked={selectedUsers.has(u.id)} onChange={() => toggleSelectUser(u.id)} />
543:                    <span className={`admin-status${u.node_region === 'peer' ? '' : ''}`} style={{ fontSize: 12 }}>
544:                      {u.node_region === 'peer' ? '对端' : '本地'}
549:                    {u.id === authUser?.id || u.node_region === 'peer' ? (
577:                      onClick={() => { setDetailTarget(u); ... adminAPI.getUserDetail(u.id) ... }}
584:                      disabled={u.node_region === 'peer'}
588:                    {u.node_region !== 'peer' && (
762:            await adminAPI.disableUser(user.id)      （禁用确认框，只能从操作列进入）
```
所有写入口都从三处进入：操作列（:577–:610）、勾选框（:535–:538）、全选（:445、:517）。本 spec 把这三处统一收到一个判定上。

## 规模表
| 数据源 | 上限 | 展示策略 |
|---|---|---|
| 联邦用户列表（本地 + 对端） | 无上限，全量一次拉取（现状，`admin.py:62`，本段不改） | 整表渲染；对端行只读 |

## 已查实的约束
1. 只改 `web/frontend/src/components/AdminPanel.jsx` 与测试；不改后端、不改 `client.js`（后端接口本来就只该管本节点）。
2. 「是不是对端行」只在一处判：模块级 `isPeerRow(u)`；行的身份是 (节点, id)：`rowKey(u)`。组件里不再手写 `u.node_region === 'peer'`。
3. 本段不涉及任何后端调用路径，通道 × 执行上下文表不适用。
4. 已有测试 `AdminPanelRoleSelect.test.jsx` 的对端夹具带了 `role: 'user'`，与真实白名单不符 → 去掉该字段，断言改为「—」。

## 步骤（一个 commit）
1. **[web/AdminPanel]**
   - 模块级加 `isPeerRow`、`rowKey`（注释写明：同 id 可同时出现在两端，本页写接口都是本节点的）。
   - 可勾选集合抽成一个 `selectable`（排除对端、自己、管理员），全选与表头勾选状态都用它（原来 :445 与 :517 各写一份）。
   - `<tr key={rowKey(u)}>`；勾选框：对端行与自己一样不渲染。
   - 角色格：对端行显示「—」；自己那行照旧显示文字；其余照旧下拉。
   - 操作列：对端行只显示「请在对端节点操作」，不渲染任何按钮；本地行照旧（去掉原来散落的 `disabled={u.node_region === 'peer'}` 与 `u.node_region !== 'peer' &&`）。
2. **[tests]** 新增 `AdminPanelPeerRows.test.jsx`（4 条，见矩阵）；改 `AdminPanelRoleSelect.test.jsx` 的对端夹具与一处断言。

## 调用点矩阵
| 调用点 | 可观测输出 | 测试 |
|---|---|---|
| 操作列（详情/禁用/邮箱/重置密码/删除） | 对端行 0 个写按钮 + 提示；同 id 本地行 5 个照常 | `AdminPanelPeerRows::对端行不给任何写入口…` |
| `<tr key>` | 同 id 两行不报重复 key | `AdminPanelPeerRows::同 id 的本地行与对端行各是一行…` |
| 勾选框 + 全选 + 批量删除入口 | 对端行无勾选框；全选只选本节点可删的 1 人 | `AdminPanelPeerRows::对端行没有勾选框…` |
| 角色格 | 对端「—」；本地可下拉；自己只读 | `AdminPanelPeerRows::对端行角色显示「—」`、`AdminPanelRoleSelect::自己那行与对端行不渲染下拉…` |
| 禁用确认框（:762） | 只能从操作列打开，对端行打不开 | 同第一行 |

## 测试（本地只跑受影响的文件加 `npm test`；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
受影响文件：`src/components/__tests__/AdminPanelPeerRows.test.jsx`、`src/components/__tests__/AdminPanelRoleSelect.test.jsx`；另跑 CI 口径 lint：`npx eslint . -c eslint.ci.config.js --quiet`。

## 对账表（沙箱预跑，`be4ad239` + 本改动；红源按名点出）
改前（新测试、旧组件）：5 failed / 2 passed。改后：7 passed；`npm test` 全量 55 文件 270 条全绿；CI 口径 lint exit 0；`AdminPanel.jsx` 全量 lint 问题数改前 16、改后 16（未新增）。

| 变异 | 预跑红源 |
|---|---|
| `isPeerRow` 恒为 false | 对端写入口、对端勾选、对端角色「—」、RoleSelect 的对端行 —— 4 条 |
| `key` 退回 `u.id` | 仅「不报重复 key」 |
| `selectable` 不排除对端 | 仅「对端行没有勾选框…」 |
| 操作列不按对端分流 | 仅「对端行不给任何写入口…」 |
| 角色格回退为 `roleLabel(u.role)` | 「对端角色「—」」+ RoleSelect 的对端行 —— 2 条 |

执行方落位后按同样 5 个变异复跑，红源不一致就停下报告。

## skill
`@search-first`（S0 复核坐标）、`@tdd`（先写 4 条看红）、`@verification-before-completion`（贴原始输出）。

## 范围规矩
新发现的问题属于本段改动面的直接修；会撞车或需 Shiyu 拍板才停。**不在本段**：跨节点禁用/启用、`set_user_disabled` 找不到用户静默成功、能禁用自己 —— 这三项归下一份 spec。

## S0（只读；全部成立直接编码）
1. `Test-Path docs/specs/admin-peer-rows.md` 为真，`Get-FileHash` 与下载件一致；`git fetch origin`，确认基线是 origin/main 顶端，复核「全量扫描」的行号（有漂移按新行号做，不停）。

## 验收
视觉只多一行提示文字、少几个按钮，不单独截图；部署后你打开用户管理页看一眼对端行即可。

## 补充：审计结论（2026-09-30）
- 934bffe 落位后，「节点」列（:552–:553）仍手写 `u.node_region === 'peer'`，违反「已查实的约束」第 2 条（对端判定只在 `isPeerRow` 一处）；且 `admin-status${… ? '' : ''}` 两分支同值，是死三元。
- 已在本段改动面内直接修：5406199 `refactor(admin): 节点列去掉死三元、对端判定统一走 isPeerRow`。className 固定为 `admin-status`，文字改用 `isPeerRow(u)`；行为不变。
- 复核：`git grep -n "node_region" origin/main -- web/frontend/src/components/AdminPanel.jsx` 只剩 :49（`isPeerRow` 定义本身）。
- 代码经 PR #77 合入 main（cae5510f）。
- 不在本段（归下一份 spec）：跨节点禁用/启用、`set_user_disabled` 找不到用户静默成功、能禁用自己。
