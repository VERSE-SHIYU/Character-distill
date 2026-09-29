# 回复流中切走再回来，对话卡住（2026-09-29，Shiyu 实测复现）

> 放到 worktree 的 `docs/specs/chat-stream-ownership.md`，以后的补充写进本文件。
> **执行方**：distill-mainline 窗口，从 origin/main（基线 `be4ad239`）新开分支 `fix/chat-stream-ownership`（`--unset-upstream`）。只改前端 store。

## 目标
回复还在生成时切到别的页面或别的会话，再回来不会卡住：发送按钮能用，回复落在它自己的气泡里。

## 根因（沙箱在基线上用 store 级测试复现，5 条红，见对账表）
`web/frontend/src/store/useAppStore.js` 的回复流（`sendMessageStream` `:1440`、`_sendRevokeNotice` `:1579`）有两处设计缺陷：
1. **「发送中」的解锁挂在「会话没换」上。** 唯一解锁点 `settle` 开头是 `if (get().sessionId !== streamSessionId) return`（`:1466`、`:1593`）。切会话的动作先掐断流、再换 sessionId，掐断后的收尾帧认出会话变了就直接返回，`sending` 永远停在 true；输入框 `disabled={sending}`（`ChatArea.jsx:779`）→ 卡住。`selectCard`（`:1114`）不重置 `sending`，`resumeSession`（`:1665`）既不掐流也不重置。
2. **流往「列表最后一条」写字。** token 与收尾都按 `msgs[msgs.length - 1]` / `-2` 改消息。从首页「继续对话」回到**同一个**会话时，`resumeSession` 用服务器那份历史（回复还没落库）替换列表，后台的流接着把字拼进用户自己那条消息里（实测：`["user","你好在的"]`）。
刷新会从服务器重建全部状态，所以「重启就好」。

## 修法（根因级，不按页面打补丁）
- **流有自己的身份**：`_chatStream = { cid, cancel }`，cid 是它要写的那条角色气泡（`withCid` 已有）。token 与收尾**只改 cid 对应的气泡**；气泡不在当前列表里（会话换了 / 重载了）就不改。
- **「发送中」归流所有**：收尾只看「我还是不是当前那条流」（`_chatStream.cid === 自己`），不再看 sessionId；是就解锁，不是就什么都不做（迟到帧不会解锁别的流、不会改别的会话）。
- **放下流只有一个入口** `_cancelChatStream()`：先同步交出所有权（清 `_chatStream`、`sending: false`），再 abort。原来 4 处 `_chatStreamCancel?.()`（`:763`、`:1097`、`:1132`、`:1190`）全部改调它；`resumeSession` 换到别的会话前也调它（原来漏了）；两个发流函数开头也调它。
- **回到正在生成回复的同一会话**：`resumeSession` 发现目标就是当前会话且有在途流，只切回聊天页、不重载（与 `selectCard` / `startChat`「同卡复用」的既有做法一致）。

## 出处对照表
| 条目 | 出处 | 本 spec 行为 |
|---|---|---|
| `AbortController.abort()` 后 `fetch` 以 `AbortError` 拒绝（异步，晚于调用方后续的同步代码） | MDN「AbortController.abort()」；本仓 `api/client.js:285-289` 把它转成 `onError` | 行为 3（先同步交出所有权再 abort） |
| 输入框由 `sending` 锁定 | `ChatArea.jsx:779` `disabled={sending}` | 行为 1 |
| 同卡复用不重载 | `useAppStore.js` `selectCard` / `startChat` 的「Reuse existing session if same card」 | 行为 4 |

## 全量扫描（基线 `be4ad239`，原始输出）
```
$ grep -n "sending" web/frontend/src/store/useAppStore.js
299:  sending: false,
1218:      sending: true,
1231:          set({ _pendingChatCardId: null, error: '缺少角色信息，无法创建会话', sending: false })
1250:      set({ _pendingChatCardId: null, error: err.message, sending: false })
1270:      sending: false,
1303:      sending: false,
1336:      sending: true,
1364:        sending: false,
1373:      set({ _pendingChatCardId: null, error: err.message, sending: false })
1389:    setScoped({ messages: [...messages, userMsg], sending: true, error: null })
1418:        return { messages: applyFlushReport(msgs, data), sending: false }
1434:        sending: false,
1454:    set({ messages: [...messages, userMsg, charMsg], sending: true, error: null })
1487:        const next = { messages: applyFlushReport(msgs, payload), sending: false }
1561:      sending: false,
1586:    set((s) => ({ messages: [...s.messages, charMsg], sending: true }))
1603:        const next = { messages: applyFlushReport(msgs, payload), sending: false }

$ grep -rn "_chatStreamCancel" --include=*.js --include=*.jsx web/frontend/src | grep -v __tests__
web/frontend/src/store/useAppStore.js:764:    get()._chatStreamCancel?.()
web/frontend/src/store/useAppStore.js:1097:    get()._chatStreamCancel?.()
web/frontend/src/store/useAppStore.js:1106:  _chatStreamCancel: null,  // cancel fn for in-flight SSE stream
web/frontend/src/store/useAppStore.js:1132:    get()._chatStreamCancel?.()
web/frontend/src/store/useAppStore.js:1190:    get()._chatStreamCancel?.()
web/frontend/src/store/useAppStore.js:1525:    set({ _chatStreamCancel: cancel })
web/frontend/src/store/useAppStore.js:1630:    set({ _chatStreamCancel: cancel })

$ grep -rn "selectCard(\|startChat(\|resumeSession(\|enterArchive(\|viewCard(\|selectText(" --include=*.jsx web/frontend/src/components | grep -v __tests__
（22 处入口：AuthorPage、ChatArea、MarketCardDetail、DistillTaskBar、ChatSessionList、DistillWorkbench、MarketPage、HistoryPanel、ArchiveListModal、HomePage、TextPanel、AwakeningToast、CharCard —— 全部经 store 的这 6 个 action，本改动在 action 内生效，入口不用改）
```
`sendMessage`（非流式，`:1384`）只有 `store/scope.test.js` 在用，组件不调，不在本段。

## 规模表
不涉及列表或数据源上限。每次发送只有一条在途流（`_chatStream` 单值），与现状一致。

## 已查实的约束
1. 只改 `web/frontend/src/store/useAppStore.js` 与新增测试；不改 `ChatArea.jsx`、`api/client.js`、后端。
2. `ChatArea.jsx:827/:843` 在「重置对话 / 撤回」前直接调 `cancelStreamRef.current()`：本改动下旧流仍是当前流，收尾帧照常解锁；随后 `resetChat` / `revokeMessage` 自己也写 `sending: false`，行为不变。
3. 切走时 abort 在途流是**现有**行为（切卡、切书本来就 abort）；本改动只是让 `resumeSession` 换到别的会话时也 abort。后端在客户端断开时是否仍把这条回复落库，本段未核（`web/routers/chat.py` 无显式断开处理），不改。
4. 本段只动前端 store，不涉及后端调用路径；通道 × 执行上下文表不适用。

## 步骤（一个 commit）
1. **[store]** 按「修法」四条改 `useAppStore.js`：新增 `_chatStream` 与 `_cancelChatStream`（替掉 `_chatStreamCancel`）；两条流的 token / settle 改为按 cid 认气泡、按归属解锁；4 处旧取消点 + `resumeSession` + 两个发流函数开头改调 `_cancelChatStream()`；`resumeSession` 加「同会话在途只切回」。沙箱 diff：58 增 50 删。
2. **[tests]** 新增 `web/frontend/src/store/chatStreamSwitch.test.js`（7 条，mock `streamSSE`：cancel 与真实实现一致，abort 后异步走 `onError`）。

## 调用点矩阵
| 调用点 | 可观测输出 | 测试（`chatStreamSwitch.test.js`） |
|---|---|---|
| `resumeSession` → 别的会话 | 切过去即解锁、旧流被 abort | 从首页/历史「继续对话」进入另一个会话… |
| `selectCard` → 别的卡 | 解锁 | 从卡片列表切到另一个有会话的角色… |
| `selectCard` → 同一张卡 | 回复落回原会话、解锁（回归） | 去首页再点同一个角色回来… |
| `resumeSession` → 同一会话、回复在途 | 不重载；回复在自己的气泡 | 后台的流不把字接到服务器刚载回的最后一条消息上 |
| `settle`（迟到帧） | 不解锁新流、不改新会话消息 | 被放下的旧流迟到的 done 帧… |
| `_sendRevokeNotice` | 同样按归属解锁 | 撤回通知那条流同样归属自己… |
| token 写入 | 只写自己的气泡 | 回复途中列表末尾多了别的消息… |
| `viewCard` / `selectText` / `startChat` | 经 `_cancelChatStream` 解锁（与 selectCard 同一入口） | 由 `_cancelChatStream` 的两条用例覆盖 |

## 测试（本地只跑受影响的文件加 `npm test`；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
受影响文件：`src/store/chatStreamSwitch.test.js`、`src/store/scope.test.js`、`src/store/terminalFrameReport.test.js`、`src/store/flushPendingKeys.test.js`；CI 口径 lint：`npx eslint . -c eslint.ci.config.js --quiet`。

## 对账表（沙箱预跑；红源按名点出）
改前（新测试、旧 store）：5 failed / 2 passed —— 红源：resumeSession 换会话、selectCard 换卡、同会话重载、撤回通知流、写自己的气泡。改后：7 passed；`npm test` 全量 55 文件 273 条全绿；CI 口径 lint exit 0；`useAppStore.js` 全量 lint 问题数改前 5、改后 5。

| 变异 | 预跑红源 |
|---|---|
| `_cancelChatStream` 不解锁 `sending` | resumeSession 换会话、selectCard 换卡、撤回通知流 —— 3 条 |
| `resumeSession` 换会话前不放下在途流 | 仅 resumeSession 换会话 |
| 同会话在途照样重载 | 仅「同会话重载」 |
| token 写「最后一条」 | 仅「写自己的气泡」 |
| `settle` 不认归属 | 仅「迟到帧」 |
| 撤回通知流不登记归属、不先放下旧流 | 仅「撤回通知流」 |

执行方落位后按同样 6 个变异复跑，红源不一致就停下报告。

## skill
`@search-first`（S0 复核坐标）、`@tdd`（先写 7 条看红）、`@verification-before-completion`（贴原始输出）。

## 范围规矩
新发现的问题属于本段改动面的直接修；会撞车或需 Shiyu 拍板才停。

## S0（只读；全部成立直接编码）
1. `Test-Path docs/specs/chat-stream-ownership.md` 为真，`Get-FileHash` 与下载件一致；`git fetch origin`，确认基线是 origin/main 顶端，复核「全量扫描」的行号（有漂移按新行号做，不停）。

## 验收
部署后你照原样试一次：发消息 → 回复还在出字时切去首页 → 从「最近对话」点回来；再试一次切到别的角色再回来。两次都不卡即通过。

## 补充（2026-09-29 审计 + 自查，执行方交付 `b38436b1` 后）

### A. 自查：本 spec 自己漏了什么（对照 8 条规矩）
| 规矩 | 原稿的问题 | 后果 |
|---|---|---|
| ④ 调用点矩阵的「列」要覆盖全部可观测输出 | 列只取了故障症状（解锁、写哪条气泡），没把「流拥有的全部状态」列全：`_chatStream` 结束后是否清空、收尾帧写的用户 id / 角色 id / summary 位置 / retracted / 语音合成的下标 / error | 下面缺陷 1、2 漏测；6 个变异全是从我自己的实现里挑的，杀光了也证明不了这些格子 |
| ② 全量扫描贴原始输出 | 入口写成「（22 处入口：…）」的摘要 | 实为 **28** 处（原始输出见 C 节），数字就是错的 |
| 路径机制清单（往已有路径加东西前列出已有机制与前提） | 没列。本改动等于给回复流加了一把「所有权锁」，路径上已有 `sending`、`_chatAbort`、`bumpScope`/`scoped`、`_pendingChatCardId`、ChatArea 的自动建会话 effect 与 `cancelStreamRef` | 约束 2「ChatArea 直接 cancel、行为不变」是错的，见缺陷 3 |

### B. 缺陷与修法（全部只动 `useAppStore.js`；沙箱在 `b38436b1` 上实测）
1. **流正常结束后没交出所有权**：两处 `settle` 的 `next` 只写 `sending: false`，`_chatStream` 留着已结束的流 → 之后「继续对话」回同一会话被短路，不再从服务器重载。**修**：`next` 同时写 `_chatStream: null`。
2. **语音合成仍按「最后一条」取下标**（`:1523`、`:1633` 的 `messages.length - 1`）：回复途中列表末尾多了别的消息，语音挂到别的消息上。**修**：两处都按自己的 cid 找下标，找不到就不合成。
3. **组件拿到的取消函数绕过了唯一入口**：`sendMessageStream` / `_sendRevokeNotice` 返回的是原始 `cancel`，ChatArea 在「重置对话 / 撤回」前直接调它（`ChatArea.jsx:827`、`:843`）→ abort 后的收尾帧仍被认作当前流 → 写 `error: '请求超时，请重试'`，用户确认重置后冒出一条假的超时提示。**修**：两个发流函数改为返回「仍是当前流才走 `_cancelChatStream()`」的函数；ChatArea 不用改。
4. **可维护性**：按 cid 改气泡的写法在两个发流函数里各一份，收成 store 内一个共用小函数。

### C. 全量扫描补原始输出（`bcd535fa`）
```
$ git grep -n "selectCard(\|startChat(\|resumeSession(\|enterArchive(\|viewCard(\|selectText(" -- web/frontend/src/components | grep -v __tests__ | wc -l
28
```
路径上已有机制与本改动的关系：`_chatAbort`（建会话请求的 abort，独立，不动）；`bumpScope`/`scoped`（sessionId / 卡 / 文本一变即作废在途的 scoped 写；`settle` 用的是普通 `set`，不受影响）；`_pendingChatCardId`（防重复建会话；`resumeSession` 的短路不碰它）；ChatArea 自动建会话 effect（条件含 `!sessionId`；短路保留 sessionId，不触发）；`cancelStreamRef`（缺陷 3，已收进唯一入口）。

### D. 补的测试（新文件 `web/frontend/src/store/chatStreamFrames.test.js`，8 条，参考实现附在同目录下载件 `chatStreamFrames.test.js`）+ 原补充里「流结束后回同一会话照常重载」1 条
| 可观测输出 | 用例 |
|---|---|
| done：用户 id、角色 id 各落本轮（末尾有别的消息） | done：本轮用户消息与角色气泡各自拿到 id |
| summary 插在本轮用户消息前 | done 带 summary：摘要插在本轮用户消息之前 |
| retracted 标在自己的气泡 | done 带 retracted：标在自己的气泡上 |
| 错误帧：error、解锁、清所有权 | 错误帧：写 error、解锁、交出所有权 |
| 语音下标（发送 / 撤回通知） | 语音开启：合成的是自己那条气泡；撤回通知开启语音：合成的是它自己的气泡 |
| 撤回通知 id | 撤回通知的 done：id 落在它自己的气泡 |
| 组件取消走唯一入口 | 组件拿到的取消函数也走唯一入口：取消后不冒出「请求超时」，发送解锁 |
| 流结束后回同一会话重载 | 回复已经跑完后，再从首页「继续对话」回到同一会话：照常从服务器重载 |

### E. 对账表续（沙箱预跑，`b38436b1` + B 节修法；受影响 4 文件 + 新文件共 25 条）
在 `b38436b1` 上（不修）：3 红 —— 错误帧交出所有权、两条语音下标（「回同一会话重载」另在原测试文件里红）。修后：25 passed；`npm test` 全量 56 文件 281 条全绿；CI 口径 lint exit 0。

| 变异 | 预跑红源 |
|---|---|
| M7 `settle` 不清 `_chatStream` | 错误帧交出所有权（+ 回同一会话重载） |
| M8 用户消息按 `length-2` 定位 | done 各自拿到 id |
| M9 summary 插在 `length-2` | summary 位置 |
| M10 retracted 标最后一条 | retracted |
| M11 语音用最后一条 | 两条语音下标 |
| M12 撤回通知 id 落最后一条 | 撤回通知 id |
| M13 返回原始 `cancel` | 组件取消走唯一入口 |

M1–M6 照旧复跑。
