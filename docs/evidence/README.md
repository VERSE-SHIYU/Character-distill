# docs/evidence — 证据产物落点

> 本目录是**证据产物**（被仓库文档正文引用的数字，其原始产物）的唯一落点。
> 写入只走一个出口：`tests/perf/evidence_writer.py`。

## 为什么单独开一个目录（机制，不是习惯）

在此之前仓里只有两类东西：生产代码，和 gitignored 的一次性 scratch。探针各自选落点
（`PROBE_OUT_DIR` / `PROBE_OUT` / stdout 重定向成 `.txt`），默认值一律指向 gitignored 目录。
于是「文档数字必须可追溯」与「调试脚本不入 main」每引用一个数字就撞一次，撞一次临时开一次例外
（2026-09-11 那次思考证据档入库是第一次）。

**不消灭「例外」这个动作，修完必然长回来**，所以改的是让例外得以存在的机制：

| 维度 | 做法 |
|---|---|
| 隔离 | 本目录（结论文档 + 原始产物 JSON）。`pytest.ini` 的 `testpaths = tests` 不收集 `docs/`，无需 ignore 例外 |
| 抽象 | 唯一写入出口 `tests/perf/evidence_writer.py`：探针不自选路径、不自行 `json.dump` |
| 复用 | `manifest.json` 是唯一真源；正文按 `id` 引用，清单只是被渲染，不是第二份手写表 |

## 落点规则（硬）

- 产物一律 `docs/evidence/<id>.json`，`<id>` 即清单条目的 `id`
- **不提供路径参数，不读环境变量**。留口子就等于留回退路径——`PROBE_OUT_DIR` 的默认值
  就是这么变成 gitignored 的。锁测试扫出口源码，出现 `os.environ` / `PROBE_OUT` 即红
- 产物必须在 git 管理下：`artifact` 被 `git ls-files` 命不中 → 锁红。
  **跑完探针要把产物一并提交**，否则就是「该转正没转」
- 探针脚本本身留 `tests/perf/`（它是可执行验证工具，不是产物）

## 正文引用语法

正文不写裸路径，写：

```
（证据：`ev:<id>`）
```

`<id>` 必须同时出现在 `manifest.json` 与 `evidence_writer._ALLOWED_BY_ID`（未注册即红）。

**渲染**：「哪些数字能写进简历」由清单渲染成 `resume-numbers.md` ——
`python tests/perf/render_evidence.py`。渲染产物**勿手改**：锁会重渲染一遍与盘上文件逐字节比对，
清单改了不重跑就红。正文的出处列写 `ev:<id>` 而不是手抄路径，也是同一个理由（手抄必漂移）。

**保留例外**：`git log` 能直查的 commit hash 不入清单——它本身就可追溯，入清单是冗余
（`engineering-evidence.md` 现有的 `✅ 代码核实` 行即此类）。

## 清单条目 schema

```json
{
  "id": "<kebab-case>",
  "claim": "一句话说清这个数字主张什么（含口径与 n）",
  "status": "verified | runtime-measured | unverifiable",
  "artifact": "docs/evidence/<id>.json 或 null",
  "script": "tests/perf/<probe>.py 或 null",
  "script_role": "producer | corroborating | null",
  "reproduce": "一条能粘进终端的复现命令或 null",
  "assertions": [{"path": "summary[1].out_tokens_max", "value": 2097}],
  "env": "模型 / 供应商 / 参数 / 任何影响数字的前置",
  "measured_at": "YYYY-MM-DD",
  "code_sha": "产出时的 commit",
  "redacted_fields": ["本产物已知被移除的顶层键"],
  "notes": "口径落差、扫描方法、或为什么现在复现不了"
}
```

### `assertions`：`claim` 里的数字必须落在产物上

`claim` 是唯一会渲染进 `resume-numbers.md` 的字段。在 `assertions` 之前它是**自由文本** ——
清单可以声称任何数字，没人比对。口径混用那次（合并中位数 vs 分档中位数，算出 `6487 / 1205`
而正确值是 `8191 / 1245`）就是这么漏过去的，靠人工发现。现在是结构上的门：

- `verified` 必填**非空**列表；`runtime-measured` / `unverifiable` 必须 `null`（与 `artifact`、`script_role` 同规则）
- 每条 `{"path": ..., "value": V}`：按 `path` 从 `docs/evidence/<id>.json` 取值，**严格相等**
  （比类型 —— `True == 1` 在 Python 里成立，不比类型会漏掉真变异）。`path` 语法：`a.b[0].c`
- `{"path": ..., "len": N}`：取容器长度，给「8 格」「14 条」「42 个键」这类计数用
- **`claim` 里每个数字**要么被某条 `value`/`len` 覆盖，要么写进 `notes` 的 **`派生量：`** 块。
  该块是笔记**最后**的一段，块内必须含 `=`（写明算法），块内数字才算已声明。
  合并中位数、按状态过滤的计数这类**产物里没有直接字段**的量，走派生量块，不假造一条断言

**口径对照写在哪**（硬要求）：文档表格名与产物字段名不一致时，`assertion` 记**产物字段名**，
`claim` 写文档口径，`notes` 记对照。`AGENTS.md` 那次 `8191 / 1245`（合并后 nearest-rank 上中位）
与 `summary[].out_tokens_p50`（分档下中位）就是两个口径 —— assertion 钉产物字段，两者靠 notes 显式
对照。这不是形式主义，是那次事故的直接产物。

**止于何处**：「`producer` 脚本跑出来是不是**真**这份产物」静态不可判 —— 查到「脚本在库、
数字与产物相等」为止。再往上就得真跑脚本，那是 LLM 调用、非确定性、且可能重跑顶替旧结论。
这个边界是刻意的，不是漏掉的。


### `script_role`：`script` 有两种语义，必须逐条表态

| 值 | 含义 | 用在 |
|---|---|---|
| `producer` | 跑 `script` 会**重生成**这份产物 | 绝大多数 `verified` |
| `corroborating` | `script` 只覆盖**同一断言**，不产这份产物 | 产物出自未入库的一次性 scratch 脚本（先例：`incomplete-v5`） |
| `null` | 没有 `script` | `runtime-measured` / `unverifiable` 两档 |

**为什么这是一个字段而不是一句散文**（与缺陷 14 同根）：`verified` 的定义是「产物在仓库、脚本可
重跑」，但「可重跑」有两种意思 —— 「跑它能得到这份产物」和「跑它能验证同一个结论」。只写在
`notes` 里，锁看不见，下一个人照抄那个形态就会填出一条**真不可复现**的 `verified`，而套件全绿。
锁的两条：`verified` 的 `script_role` 必须是上表前两个值之一（其余两档必须 `null`）；
`corroborating` 必须带非空 `notes` 交代产出脚本是谁、为什么没入库。
迁移用的 `register_artifact` **要求这个参数且无默认值** —— 迁移路径正是最容易填错的那条。

### 三档 status（**只有三档，第四值锁测试直接红**）

| status | 含义 | 必填 | 锁校验 |
|---|---|---|---|
| `verified` | 产物在仓库、脚本可重跑 | `artifact` `script` `script_role` `reproduce` `assertions` | `artifact` 被 `git ls-files` 命中；`script` 在库；`reproduce` 路径解析得到；`code_sha` 解得开或为哨兵；`assertions` 非空且与产物严格相等 |
| `runtime-measured` | 运行时实测，非仓库数据 | `env` `measured_at` + `notes` 写明**扫描方法** | `artifact` 必须为 `null` |
| `unverifiable` | 当时结论，现已不可复现 | `measured_at` `env` + `notes` 写明**为什么现在复现不了** | `artifact` 必须为 `null` |

**`runtime-measured` 与 `unverifiable` 的分界**：环境还在、只是数据没入库 → 前者；
环境 / 快照已不存在 → 后者。有产物却标这两档 = 该转正没转，锁红。

## 「核对结论不是状态位」

`status` 只描述**可追溯性**（这个数字今天还查不查得到），不描述**核对结果**（当时核对下来对不对）。
两者必须分开：混进一档，`status` 就同时是两个维度，之后必然有人问「`✗` 的东西到底能不能写进简历」——
这个问题没有统一答案。

`engineering-evidence.md` 图例里原有第四个标记 `✗ 不符`，P0 已删除。矛盾的处置方式是
**当场订正**，不是常驻一个状态位。先例：2026-09-11 入库核对发现 `text.py` 的 403 语义与正文表述
矛盾，当日修复转正，`✗` 行随之消失。

## 写入时脱敏：白名单，不是黑名单

出口按**白名单**序列化：`_ALLOWED_BY_ID[id]` 之外的顶层键一律丢弃，并计入 `redacted_fields`
（该字段的完整含义是「本产物已知被移除的顶层键」——含写入时被白名单丢弃的，以及入库前手工删掉的，
后者靠 `notes` 与 `AGENTS.md` 追溯）。
黑名单只挡已知字段名，探针加一个新字段就漏；白名单把脱敏从「记得删」变成「结构上过不去」
（同一手法先例：`adapters/llm_adapter.py::user_facing_error` 把运维口径挡在上屏之外）。

- 白名单**只在出口里**（探针不能自己放宽），但探针可以传 `subset` 只导出更少字段：
  校验 `subset ⊆ allowed`，越界抛错。收窄不增加泄漏风险，没必要禁
- 未注册的 `id` 直接抛错；注册了但白名单为空也抛错（空集 = 什么都没保证）
- 出口拒绝缺 `claim` / `script` / `env` / `code_sha` 的调用：清单缺这些字段就不可追溯
- **`runtime-measured` / `unverifiable` 的条目：注册一个空 `frozenset()`。** 这两档没有产物
  （`artifact` 必须为 `null`），空白名单正是它们的正确登记形态 —— 结构上堵死「哪天有人往里写产物」
  这条路。空集只在**调用时**被 `write_evidence` 拒绝，注册时不拦，正是为了留这个用途

**已知上限**：白名单只管**顶层键**。嵌套容器（例如 `records[]` 的逐条字段）由探针自己保证只装
统计量——出口对它们的保证是「这个容器被整体允许或整体丢弃」，不是逐字段。若将来有探针需要更细的
粒度，再给 `_ALLOWED_BY_ID` 加 dotted path，别在这里提前建模。

**版权 / 去标识**：产物只留统计量、参数与 id，不留正文、真实角色名、作品名。
`tests/perf/map_len_probe.py` 的 `PLACEHOLDER_CHARS` 硬门是这条规则的执行点之一。

## 锁

`tests/test_evidence_integrity.py`：

1. 正文里每个 `ev:<id>` 都能在 `manifest.json` 解析到条目
2. 每个条目满足其 `status` 的必填字段；`verified` 的产物被 git 命中，另两档 `artifact` 为 `null`
3. `status` 只能是三值之一
4. 每个条目 id / `ev:` 引用都在 `_ALLOWED_BY_ID` 有注册
5. 出口不接受路径类参数、不读环境变量（落点不可覆盖）
6. **指称闭合**：`script` 在库（两种 `script_role` 都查）；`reproduce` 里的仓内路径逐个解析；
   `code_sha` 解得开一个 commit 或精确等于哨兵 `unknown(scratch)`；`notes`/`claim` 里的路径
   tracked，或属已知 gitignored 前缀且**同条目内**带非仓库标注
7. **数值闭合**：见 `assertions` 一节 —— 非空、与产物严格相等、`claim` 数字全覆盖

`tests/test_evidence_integrity_mutations.py`：**变异矩阵**。每条清单语义断言必须有自己的
专属红源 —— 每个变异**只跑指定的那一条**断言，证明它不靠渲染锁兜住。渲染新鲜度锁会因为
**任何**清单变异而红，没有这个矩阵，新增断言的「绿」可能是假的。**此后新增任何清单语义
断言，必须同时补一行矩阵。**
