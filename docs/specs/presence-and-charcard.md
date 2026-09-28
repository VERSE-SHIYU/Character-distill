# Spec：他人主页在线状态与页头 + 角色管理页空态缺页头

基线 `main` `4c2ffbb`。只改前端。改动已随 PR #54 合入 main（merge e0fd868d）；原 .diff 未入库，以 PR 为准。本文件取代此前单独发出的 `presence-fix.md`、`charcard-empty-header.md`（未入库，直接作废）。

## 问题与根因
1. **他人主页显示的是自己的在线状态**：`MinePage.jsx:277` 用 `authUser.id` 查询；页面对自己和他人共用，应查当前展示的人。同一查询在 `PrivateMessageChat.jsx:313` 另写一份。
2. **他人主页页头贴边**：`MinePage.jsx:547` 的 `PageHeader` 不在 `.panel` 内，缺 `.panel` 的内边距（`global.css:1986-1987`）。
3. **角色管理页没选文本时没有页头**：`CharCard.jsx:52` 提前返回空态，页头只在另一分支（`:83`），空态没有标题、返回、滑动返回、蒸馏工作台入口。

## 库选型（规则：先找库）
在线状态是「按 id 取数、换人切换、定时刷新」的服务端数据，属于数据请求库的本职，不自写。`npm view` 2026-09-28：

| 库 | 版本 | 许可 | React 19 | 接入 |
|---|---|---|---|---|
| **SWR（采用）** | 2.5.1 | MIT | 支持 | 零配置 |
| TanStack Query | 5.104.0 | MIT | 支持 | 根组件需加 Provider |

采用 SWR：这次只有一个只读接口加定时刷新，SWR 零配置即可；为「以后可能统一数据层」提前引入更重的库违反 YAGNI。项目目前没有任何数据请求库，其余请求不在本次迁移范围。

## 已查实约束（`4c2ffbb` 上现查，S0 原样重跑比对）
```
echo "== 在线状态接口的全部前端调用"; grep -rn "/online\`" src --include=*.js --include=*.jsx | grep -v __tests__
echo "== 接口隐藏时的返回"; grep -n "hidden" ../../web/routers/auth.py
echo "== .panel 内边距与头部间距"; grep -n "^\.panel {" -A8 src/styles/global.css
echo "== 他人主页页头"; grep -n "<PageHeader" src/components/MinePage.jsx
echo "== CharCard 空态分支与页头"; grep -n "if (!currentTextId)\|<PageHeader\|shell-placeholder\b" src/components/CharCard.jsx
echo "== 项目现有数据请求库"; grep -nE '"(swr|@tanstack/react-query)"' package.json || echo "（无）"
```
```
== 在线状态接口的全部前端调用
src/components/MinePage.jsx:277:      const res = await fetchWithTimeout(`/api/auth/user/${authUser.id}/online`)
src/components/PrivateMessageChat.jsx:313:      const res = await fetchWithTimeout(`/api/auth/user/${otherUserId}/online`)
== 接口隐藏时的返回
913:        return {"online": None, "last_active_at": None, "hidden": True}
926:        "hidden": False,
== .panel 内边距与头部间距
1981:.panel {
1982-  flex: 1;
1983-  display: flex;
1984-  flex-direction: column;
1985-  overflow: hidden;
1986-  padding: 28px 32px 24px;
1987-  gap: 20px;
1988-}
1989-
== 他人主页页头
547:      {currentView !== 'mine' && <PageHeader title={username} onBack={popView} />}
== CharCard 空态分支与页头
52:  if (!currentTextId) {
54:      <div className="shell-placeholder">
55:        <div className="shell-placeholder-inner">
56:          <div className="shell-placeholder-icon"><User size={28} /></div>
57:          <div className="shell-placeholder-title">
60:          <div className="shell-placeholder-sub">
83:          <PageHeader title="角色管理" onBack={goBack} actions={<DistillWorkbenchButton />} />
120:          <PageHeader title={currentCard.name || '角色详情'} onBack={() => viewCard(null)} />
== 项目现有数据请求库
（无）
```

## 改动
1. 依赖：`swr@^2.5.1`
2. `hooks/usePresence.js`：`useSWR` 的薄封装，`usePresence(userId, { refreshInterval })` → `{ online, hidden, lastActive }`；无 id 不请求。缓存、换人切换、过期响应都由 SWR 处理
3. 私信页改用 `usePresence(otherUserId, { refreshInterval: 30000 })`，删掉原来的查询函数与 `setInterval`
4. MinePage 改用 `usePresence(userId)`；隐藏时接口返回 `online: null`，渲染条件 `online !== null` 自然不显示
5. `global.css`：`.panel` 的内边距与间距改为 `:root` 上的 `--panel-pad-top/--panel-pad-x/--panel-pad-bottom/--panel-gap`（数值不变）；`.mine-page-v2 > .page-header` 用同一组变量，不复制数值
6. `CharCard.jsx`：页头只一份，有无文本都渲染；「当前文本」一行只在有文本时显示；内容区在空态与 `CharPanelBody` 间切换

触发链：进入某人主页 → `authorUserId` 变 → `userId` 变 → SWR 切到对方的 key 请求 → 显示对方状态（隐藏则不显示）。没选文本进入角色管理 → 页头照常渲染 → 点返回走 `goBack`。

## 测试与验收
- 单测：`usePresence.test.jsx` P1–P6、`MinePagePresence.test.jsx` M1–M3、`PrivateMessageChatPresence.test.jsx` D1–D3、`CharCardHeader.test.jsx` E1–E3
- 验收（写进仓库现有脚本）：`mine-profile-verify.cjs` 新增 `runOtherProfile`（对方 id、隐藏不显示、标题左边距与回收站一致，桌面 + 手机）；`char-card-verify.cjs` 新增 `runEmptyState`（空态标题、返回、入口、内容可见、返回能离开，桌面 + 手机）
- 本地只跑上述新测试与 PrivateMessageChat / MinePage / CharCard 既有测试、`npm test`、eslint；两个验收脚本在 docker 环境跑（`docker compose -f docker-compose.local.yml up -d --build postgres app`，先查端口 / 容器名 / 数据卷冲突并记入报告，跑完 `down` 不加 `-v`）。合并门是分支 CI，合并只做 git 操作

## 实测（沙箱：Vite 开发服务器 + 登录 mock）
`npm test` 277/277；CI 口径 `npx eslint src -c eslint.ci.config.js --quiet` exit 0（`npm run lint` 在 main 上原本即红，非本改动引入）；两个验收脚本修复后 PASS。

## 对账表（调用点 × 可观测输出；全部预跑）
| 调用点 | 可观测输出 | 守它的测试 | 变异 → 结果 |
|---|---|---|---|
| MinePage | 查的是展示对象的 id | M1、验收 | 换回 main 原文件 🔴；改用自己的 id 🔴 |
| MinePage | 对方隐藏时不显示 | M2、验收 | 同上 🔴 |
| MinePage 页头 | 左边距与二级页一致 | 验收（桌面 + 手机） | 删页头规则 🔴；水平变量改错 🔴 |
| 私信页 | 查对方 id、30 秒刷新 | D1 | 查错 id 🔴；不轮询 🔴 |
| 私信页 | 隐藏时不显示状态行 | D3 | 条件写死为真 🔴 |
| usePresence | 定时刷新 | P6 | 不传 refreshInterval 🔴 |
| usePresence | 无 id 不请求 | P4 | 🔴 |
| usePresence | hidden 透出 | P2 | 🔴 |
| CharCard 空态 | 有页头、返回、入口 | E1、E2、验收 | 换回 main 原文件 🔴；空态不渲染页头 🔴 |
| CharCard 空态 | 内容可见 | E1、验收 | 内容被隐藏 🔴 |

已验证：两个验收脚本在 docker 构建版（docker-compose.local.yml）上实跑 PASS，pageErrors 为空（PR #54）。
