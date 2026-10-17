# Spec：原则卡（principles-card）

> 规矩见 `docs/specs/SPEC-STANDARD.md`。基线：main `a39b1512`（2026-10-17）。小改动，流程从简。

## 0. 做什么

把 Shiyu 反复强调的 12 条工作原则放进 `.claude/rules/principles.md`。依据（Claude Code 官方文档 code.claude.com，2026-10-17 线上版）：
- memory 页：`.claude/rules/` 下没有 `paths:` 的规则每次会话都会加载；它不算 CLAUDE.md，不影响 AGENTS.md 的加载。
- context-window 页「What survives compaction」：「Project-root CLAUDE.md and unscoped rules | Re-injected from disk」——所以不需要 SessionStart Hook。

## 1. 已查实的约束（S0 复核，不成立就停）

| # | 事实 | 坐标 @ a39b1512 |
|---|---|---|
| C1 | `.gitignore` 整个忽略 `.claude/`，下一行忽略 `CLAUDE.md` | `.gitignore:230-232` |
| C2 | 仓库里没有被跟踪的 `.claude/` 文件 | `git ls-files .claude` 为空 |
| C3 | 模式 `.claude/*` + `!.claude/rules/` 只放出 `rules/` 下的文件，`settings.local.json`、`skills/` 仍被忽略 | 临时仓库实测：`git status -uall` 只列出 `.claude/rules/principles.md` |

S0 另查（贴输出，不停）：本机 `.claude/rules/` 是否已有其他文件；如有，列出来报告，不要一起提交。

## 2. 改动

1. `.gitignore:231` 的 `.claude/` 改成两行：`.claude/*` 和 `!.claude/rules/`。`CLAUDE.md` 那行不动。
2. 新增 `.claude/rules/principles.md`，内容逐字照抄附件 `principles.md`（不加 `paths:` frontmatter）。

不改其他文件，不加测试（纯文档）。

## 3. 验收

- A1 `git status` 只出现 `.gitignore` 和 `.claude/rules/principles.md`。
- A2 在本机新开 Claude Code 会话，`/memory` 列表里有 `principles.md`。
- A3 执行 `/compact` 后问「原则第 3 条是什么」，能答出原文。
- A4（之后 5 个任务）Shiyu 记录还需提醒的次数，不降就回来改卡。
- 合并：开 PR，两项必需检查绿后合并。
