# F1：重新蒸馏前确认

基线：main `9d8a4562`。分支：`feat/redistill-confirm`。只改前端。
目标：用户点「重新蒸馏」（会覆盖已有的卡，并且要花钱）时，先看到角色名和后果，确认后才发请求。第一次蒸馏、出错续跑的行为都不变。
方案依据：总文档第五节 10-19 14:10 条（Tracy 定 F1 = A）。

## 一、目标检查（第 1 个提交只放检查文件，每条用 `it.fails` 标成「预期失败」；之后每个提交都跑）

三个检查文件是现成的，已在原型上跑通，见第八节。直接放进对应位置即可：

| 文件 | 放到 | 检查项 |
|---|---|---|
| `redistillConfirm.test.js` | `web/frontend/src/store/` | G1 force=true 时先不发请求，`redistillPrompt` 记下 textId 和 name；G2 确认后发 1 次请求，`force:true`，返回 true，`redistillPrompt` 清空；G3 取消后不发请求，返回 false；G4 force=false 立即发请求，不弹窗；G5 启动失败返回 false |
| `RedistillConfirm.test.jsx` | `web/frontend/src/components/__tests__/` | G6 弹窗里写出「重新蒸馏「祈灼」？」和《书名》；点「取消」后弹窗消失，也不发请求 |
| `RedistillCharCard.test.jsx` | `web/frontend/src/components/__tests__/` | G7 在角色页点「重新蒸馏」，`distillCharacter` 返回 false 时（取消或启动失败），按钮要回到「重新蒸馏」，不能卡在「蒸馏中…」 |

G7 的原因：CharCard.jsx:282 在调用之前先执行 `setDistillingName(name)`；:235-237 只在 `distilling` 变化时才清空。取消时 `distilling` 一直是 false，名字就不会被清掉；按钮文字由 :489 的 `distillingName === name` 决定，于是一直显示「蒸馏中…」。G7 的写法照 `CharCardHeader.test.jsx`：mock 掉整个 store，`distillCharacter` 设成 `vi.fn(() => Promise.resolve(false))`。这和 G3 证明的真实返回值是同一份约定。

第 1 个提交里，三个文件的每条 `it(` 都改成 `it.fails(`；实现提交里再改回 `it(`。

## 二、S0 开工前核对（任何一条对不上就停下报告）

先用 `Test-Path docs/specs/redistill-confirm.md` 确认文件存在。

| 位置 | 应看到 |
|---|---|
| `web/frontend/src/store/useAppStore.js:870-882` | `distillCharacter: async (textId, characterName, force = false) => {` 直接 `postJSON('/api/distill/start', {...force})`，出错时 `set({ error: err.message })`，没有返回值 |
| `web/frontend/src/components/CharCard.jsx:281-284` | `handleDistill = (name, force = false) => { setDistillingName(name); distillCharacter(textId, name, force) }` |
| `web/frontend/src/components/CharCard.jsx:235-237` | `useEffect(() => { if (!distilling) setDistillingName(null) }, [distilling])` |
| `web/frontend/src/components/DistillWorkbench.jsx:388-390` | `redistill = () => { if (card.text_id) distillCharacter(card.text_id, name, true) }` |
| `web/frontend/src/components/DistillWorkbench.jsx:289-292`、`DistillTaskBar.jsx:83-87` | 调用 `distillCharacter(task.textId, task.character)`，没有传 force |
| `web/frontend/src/components/common/ConfirmModal.jsx` | props 有 `isOpen, title, message, confirmText, cancelText, onConfirm, onCancel, danger, disabled`；用 portal 挂到 body |
| `web/frontend/src/App.jsx:287` | `<AwakeningToast />` 是挂在根组件上的全局组件 |

全量扫描（基线 9d8a4562）：
```
$ grep -rn "distillCharacter" web/frontend/src --include=*.js --include=*.jsx | grep -v __tests__
web/frontend/src/components/DistillTaskBar.jsx:40 / :86
web/frontend/src/components/DistillWorkbench.jsx:250 / :291 / :377 / :389
web/frontend/src/components/CharCard.jsx:166 / :283
web/frontend/src/store/useAppStore.js:870
```
调用点只有这 4 个：CharCard:283（force 取决于这个角色是否已有卡）、Workbench:389（force=true）、Workbench:291 和 TaskBar:86（不传 force，按钮只在任务 `error` 时出现，见 `web/routers/distill.py:982`）。

## 三、改法

**store（`useAppStore.js`）**：新增 `redistillPrompt: null` 状态，以及 `confirmRedistill`、`cancelRedistill` 两个 action；`distillCharacter` 改成下面这样，**是否需要确认只在这一处判断**：

```js
distillCharacter: async (textId, characterName, force = false) => {
  if (force) {
    const ok = await new Promise((resolve) =>
      set({ redistillPrompt: { textId, name: characterName, resolve } }))
    if (!ok) return false
  }
  set({ error: null })
  try {
    const data = await postJSON('/api/distill/start', { text_id: textId, character_name: characterName, force })
    if (data.task_id) { get().addDistillTask(data.task_id, textId, characterName); return true }
    set({ error: '蒸馏启动失败' }); return false
  } catch (err) { set({ error: err.message }); return false }
},
confirmRedistill: () => { const p = get().redistillPrompt; set({ redistillPrompt: null }); p?.resolve(true) },
cancelRedistill: () => { const p = get().redistillPrompt; set({ redistillPrompt: null }); p?.resolve(false) },
```

**新组件 `components/RedistillConfirm.jsx`**：从 store 读 `redistillPrompt` 和 `texts`，渲染 `ConfirmModal`：
- `title`：`重新蒸馏「${name}」？`
- `message`：书名找得到时开头加 `《书名》：`（书名取 `t.title || t.filename`，写法同 `api/cards.js:26`），后面接 `会用新结果覆盖现有角色卡，手动修改会丢失；会重新调用模型并产生费用。`
- `confirmText="重新蒸馏"`，`danger`；`onConfirm={confirmRedistill}`，`onCancel={cancelRedistill}`

挂到 `App.jsx` 里 `<AwakeningToast />` 的旁边。

**CharCard.jsx:281-284**：
```js
const handleDistill = async (name, force = false) => {
  setDistillingName(name)
  if (!(await distillCharacter(textId, name, force))) setDistillingName(null)
}
```
第一次蒸馏照旧立刻显示「蒸馏中…」；取消或启动失败时把名字清掉。

DistillWorkbench、DistillTaskBar 不用改：它们本来就调用 `distillCharacter`，确认逻辑在 store 里统一处理。

## 四、行为对照（改前 → 改后）

| 入口 | 改前 | 改后 |
|---|---|---|
| 角色页「蒸馏角色」（第一次） | 立即开始 | 不变 |
| 角色页「重新蒸馏」 | 立即开始，覆盖旧卡 | 先弹窗；确认后才开始，取消则什么都不发 |
| 工作台卡片详情「重新蒸馏」 | 立即开始，覆盖旧卡 | 同上 |
| 工作台 / 任务栏「重新蒸馏」「继续蒸馏」（任务出错时） | 立即续跑 | 不变 |
| 草稿重转（后端接口 redraft） | 不经过前端 | 不变 |
| 启动失败 | 页面显示错误，角色页按钮会一直卡在「蒸馏中…」 | 页面显示错误不变；按钮恢复原样 |

覆盖旧卡的依据：`storage/postgres_store.py:691-697` 的 `save_card` 按 text_id + name + user_id 查到旧卡后，用 UPDATE 整张替换 card_json；`core/text_manager.py:591-593`。

## 五、结构自检

| 问题 | 答案（附代码行） |
|---|---|
| 「重新蒸馏要确认」这条规则写了几处 | 1 处：`distillCharacter` 的 `if (force)` |
| 调用方是否被迫知道不该知道的事 | 不会。4 个调用点都不知道有弹窗；只有 CharCard 用到了返回值，用来复原自己的按钮状态 |
| 以后再加一个「重新蒸馏」入口要改几处 | 0 处：只要调用 `distillCharacter(..., true)` 就自动弹窗 |
| 弹窗写了几处 | 1 处：`RedistillConfirm` 复用 `ConfirmModal`，全局只挂一个 |

## 六、验证

```
cd web/frontend
npx vitest run src/store/redistillConfirm.test.js src/components/__tests__/RedistillConfirm.test.jsx src/components/__tests__/RedistillCharCard.test.jsx src/store/cancelDistillTask.test.js src/components/__tests__/DistillWorkbenchEntry.test.jsx src/components/__tests__/CharCardHeader.test.jsx
npm test
```
不涉及后端，不跑 pytest。合并门是分支 CI。

报告内容：每个提交的 `git show --stat`；第 1 个提交 G1–G7 全部「预期失败」的原始输出（`it.fails` 显示为通过即表示确实失败了；要另外贴一次去掉 `.fails` 后 7 条全红的输出），以及最后一个提交 7 条全绿的输出；`npm test` 最后的汇总行；CI 结果。末尾附「已核实 / 未核实」。范围内的问题直接修，碰到冲突或需要决定的事项才停下。

## 七、已知、不在本 PR 处理

- 弹窗里不写具体金额：每本书长度不同，固定数字会误导。
- 记录这次是从哪个按钮发起的（entry）、是否 force，是 F2 的范围，等 ⑤b 合并后再做。

## 八、原型（我在沙箱 main 9d8a4562 上跑过）

材料（Tracy 放好；sha256 前 16 位）：

| 文件 | 放在哪 | sha256 |
|---|---|---|
| `redistill-confirm.md`（本文件） | `docs/specs/` | 见文件本身 |
| `proto-redistill-confirm.diff` | 仓库根目录，不提交 | `e9cd9992c1bd2243` |
| `RedistillConfirm.jsx` | `web/frontend/src/components/` | `22eb66fc793441e5` |
| `redistillConfirm.test.js` | `web/frontend/src/store/` | `4dfa6a7c9f0e79b7` |
| `RedistillConfirm.test.jsx` | `web/frontend/src/components/__tests__/` | `8442b0309b39bca7` |
| `RedistillCharCard.test.jsx` | `web/frontend/src/components/__tests__/` | `5591fceed9165387` |

diff 只包含改动的 3 个已有文件（App.jsx、CharCard.jsx、useAppStore.js）；新组件和 3 个检查文件单独给。

原型结果：
- 改动前：G1–G5 `Tests 5 failed (5)`；G7 在旧 CharCard 上 `Tests 1 failed (1)`（找不到名为「重新蒸馏」的按钮，因为按钮卡在「蒸馏中…」）。
- 改动后：G1–G7 `Tests 7 passed (7)`；`npm test` 全量 `Test Files 107 passed (107)`、`Tests 584 passed (584)`。

你是实现方，diff 只是参考实现。套上之后把改到的文件读一遍；和本 spec 或仓库现状对不上的地方，属于本次改动范围的直接修，并在报告里写明。

## 补充（执行）

- spec 原先没说明本文件跟哪个提交走，现随此提交加入。
- 第 1 个提交里 G6 无法表现为 `it.fails`：组件文件不存在，整个测试文件加载失败。改前实际结果是 6 条测试失败 + 1 个测试文件加载失败。

## 补充（e2e）

- 文件：`web/frontend/e2e/redistill-confirm.spec.js`
- 断言：
  1. 已有卡角色点「重新蒸馏」→ 弹窗出现，标题为「重新蒸馏「祈灼」？」并含《某书》；取消后 `/api/distill/start` 0 次，按钮仍是「重新蒸馏」。
  2. 再点一次并确认 → `/api/distill/start` 1 次，请求体 `force: true`，`character_name: '祈灼'`。
  3. 无卡角色点「蒸馏角色」→ 不弹窗，`/api/distill/start` 1 次，`force: false`。
  4. 工作台卡片详情点「重新蒸馏」→ 弹出同一个弹窗。
  5. 弹窗出现时截图：`web/frontend/e2e/redistill-confirm-modal.png`（`e2e/*.png` 由 `.gitignore` 忽略）。

## 补充（审计，10-19）

独立审计发现：合并门只验了「store 收到 force 后怎么做」（G1–G5）和「CharCard 取消后复原」（G7），没验「每个入口传对 force」和「第一次蒸馏成功后按钮不变」。根因：是否弹窗由调用方传的 force 决定（第三节），入口→force 的对应关系是目标的另一半，第一节没给检查；e2e 覆盖了其中一部分，但 CI 不跑 Playwright。

本提交补的检查：

| 检查 | 文件 | 断言 |
|---|---|---|
| G8 入口 × 是否弹窗 | `src/components/__tests__/RedistillEntries.test.jsx`（真实 store + mock 网络层） | 任务栏「重新蒸馏」「继续蒸馏」、工作台任务「重新蒸馏」「继续蒸馏」：不弹窗，直接发 1 次 `force:false`；工作台卡片详情「重新蒸馏」：弹窗，先不发请求 |
| G7b 第一次蒸馏不变 | `src/components/__tests__/RedistillCharCard.test.jsx` 新增一条 | 无卡角色点「蒸馏角色」：`distillCharacter(…, false)`；成功后按钮保持「蒸馏中…」（替身照生产：成功时把 distilling 置 true） |
| e2e 截图 | `e2e/redistill-confirm.spec.js` 第 5 条 | `page.screenshot` 加 `animations: 'disabled'`，截到淡入结束后的弹窗 |

变异（每条只改一处，跑 6 个相关文件 + 新文件）：工作台卡片详情不传 force、角色页第一次蒸馏也弹窗、工作台续跑也弹窗、任务栏续跑也弹窗、CharCard 成功后也清「蒸馏中」——补前在 `npm test` 全绿，补后全红。不补：后端无 task_id 返回 true（`/start` 只返回 task_id 或抛错，`web/routers/distill.py:959`，分支不可达）。

事实更正：第二节说任务栏 / 工作台的续跑按钮「只在任务 error 时出现」，实际 interrupted（「继续蒸馏」）也出现（`distill.py` 状态表 :980-985）；两者都不传 force，结论不变。
