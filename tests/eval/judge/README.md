# LLM-as-judge κ 复核件

人 gold 对 judge 的 per-dim Cohen's κ 复核所需的最小可复现件。**不含任何对话正文。**

## 文件作用

| 文件 | 作用 |
|---|---|
| `score_kappa.py` | 唯一复算脚本：读 `gold_final.json` + 一个 judge 标签 jsonl，算 per-dim κ、一致率、平局率、不一致明细、CRPO overall |
| `gold_final.json` | 人工 gold（reconciled 终版），16 pair × 4 维 |
| `judge_labels.jsonl` | judge 标签，qwen-max 主臂 |
| `judge_labels_rev.jsonl` | 同上，选项顺序对调（flip）的重复 pass，用于查位次偏见 |
| `judge_labels_ds.jsonl` | judge 标签，deepseek 臂（**补跑**，见下） |
| `judge_labels_ds_rev.jsonl` | 同上，flip 重复 pass |

> **`why`（judge 理由）已删除**：原始标签每行带一个 `why` 字段，里面引用了对话正文（含真实人名与原文片段），因隐私从入库件中删去。κ 只用 `d1` / `d2` / `d4` 三个标签字段计算，删 `why` 不影响任何数字。

## 复算

```
python tests/eval/judge/score_kappa.py
python tests/eval/judge/score_kappa.py --labels tests/eval/judge/judge_labels_ds.jsonl
```

按脚本所在目录解析输入路径，任意 cwd 可跑。d3（知识越界）在本语料全为 tie、不可评，脚本单独声明、不计入 κ（需专门越界探测样本）。

## 两臂结果（per-dim κ，d1 人设符合 / d2 腔调稳定 / d4 情感连贯）

| 臂 | d1 | d2 | d4 | 出处 |
|---|---|---|---|---|
| qwen-max | 0.111 | −0.091 | 0.264 | `judge_labels.jsonl`，实跑复算 |
| deepseek（补跑） | 0.158 | 0.091 | 0.168 | `judge_labels_ds.jsonl`，实跑复算 |

两臂都已由本目录的脚本与标签实跑复算（命令见上），数字可现场重跑。

## 上游流程（不入库）

「取窗 → 配对 → LLM 打标 → 人工 reconciliation → 对账」这条流水线依赖真实对话正文与生产库，出于隐私不入库。因此本目录只保留**下游**可复现件：金标 + 标签 + 算 κ 的脚本。上游的 `analyze.py` / `judge_run.py` / 各 `build_*` 脚本读正文与（已失效的）临时路径，不入库，也不在 `score_kappa.py` 的执行路径上。

## 两条历史记录说明

1. **deepseek 标签是 09-08 20:02 的补跑。** 原始 run 的 per-dim κ 记为 0.250 / 0.091 / 0.163，但其 raw 输出已被 qwen 的 run 覆盖，**不可复算**，故不采用、不引用。入库与引用的一律是上面可复算的补跑值 0.158 / 0.091 / 0.168。
2. **`judge_labels_ds*.jsonl` 的 `provider` 字段值是环境变量名（`DEEPSEEK_API_KEY`），不是密钥。** 这是当时打标脚本把 provider 记成了取 key 的变量名的历史记录错位；`model` 字段（`deepseek-chat`）才是真实模型。文件内无任何凭据值。
