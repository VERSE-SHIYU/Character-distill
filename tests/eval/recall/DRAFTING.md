# 召回题起草说明（0d）

出题分三步，锚点与核查在 SG 容器里跑（书在 SG），起草在本地做。本文件不含原文、
不含论文提示词。

## 1. 抽锚点（SG）

```bash
cd /app && python /tmp/recall_pkg/scripts/recall_questions.py anchors \
  --suite /tmp/recall_pkg/e2e/scratch/recall/suite.json \
  --out-dir /tmp/recall_pkg/e2e/scratch/recall/work
```

产物 `<text_id>_<角色>.anchors.json`：每本（只给没有人工题的行）至多 30 个锚点，
每个带 `id`（`a01`…）、`pos`、`text`（原文窗口）、`nstart` / `nend`（规范化区间）。
把整个 `work/` 目录拷回本地。

## 2. 起草（本地）

每本书 × 角色一个 `<text_id>_<角色>.jsonl`，一行一个锚点：

```json
{"id": "a01", "query": "你那年秋天到底为什么没去？", "should": ["原文里连续的一段话"]}
{"id": "a07", "skip": true}
```

- `id` 必须是该锚点文件里的 id：不许新增、不许改号。
- `skip`：这个锚点出不了题就空着（打 `skip`），**不许用别的锚点补**。
- `query`：用户**跟这个角色聊天**时会说的话，第二人称；问他经历过、说过、做过的
  事。不列选项、不自带答案、不点位置（第几章、线索在哪）。
- `should`：从该锚点 `text` 窗口里**逐字**摘 10–40 字连续原文；在全书里唯一；
  不含省略号（`……` / `...`）；落在窗口内。
- 不要照抄原句的稀有措辞：措辞评审（A.5）判 `searchable=true` 会退回。

## 3. 核查（SG）

```bash
cd /app && python /tmp/recall_pkg/scripts/recall_questions.py check \
  --suite /tmp/recall_pkg/e2e/scratch/recall/suite.json \
  --prompts /tmp/recall_pkg/e2e/scratch/recall/prompts \
  --drafts /tmp/recall_pkg/e2e/scratch/recall/drafts \
  --out-dir /tmp/recall_pkg/e2e/scratch/recall/work \
  --round 1
```

关卡顺序：台账 → g_ground（本地、免费）→ A.5 措辞评审 → A.6 核查（A.4 不用，spec ⑮）。
关 1 不过的锚点不花模型调用。产物：

| 文件 | 内容 |
|---|---|
| `verdicts.jsonl` | 每个 id 这一轮的状态、各关原始 JSON |
| `repair.jsonl` | 需要改的 id 与原因（`suggestion`） |
| `<text_id>_<角色>.jsonl` | 已录取的题，评测格式（`phase: null`，带 `own_words_flag`） |
| `<text_id>_<角色>.discarded.json` | 已丢弃的 id（下一轮不许重出） |

## 4. 修复

- 只改 `repair.jsonl` 里的 id，按 `problems` / `suggestion` 改；把改好的草稿放回
  `drafts/`，重跑 `--round 2`，仍不过再 `--round 3`。
- 第 3 次仍不过硬关 → 丢弃；丢掉的锚点保持空着。
- A.5 修两次仍判 `searchable=true` → **录取并打 `own_words_flag`**，不丢。
- `*_unparsed` 不计修复次数：工具内部对同一关重调一次，仍解析不了记
  `discarded:parse`。
