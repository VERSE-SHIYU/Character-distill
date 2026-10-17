# Spec：必守规则拆出 AGENTS.md（project-rules）

> 规矩见 `docs/specs/SPEC-STANDARD.md`。基线：main `58215653`（含 #178）。小改动，流程从简。

## 0. 为什么做

本机 Claude Code 从未加载 AGENTS.md：工作目录有一份未入库的本地 `CLAUDE.md`，官方规则是有 CLAUDE.md 就不读 AGENTS.md。那份本地 CLAUDE.md 是 AGENTS.md 第 1–52 行的旧副本，已缺 10 处（节点域名、证据产物路径、开发约束后 9 条）。AGENTS.md 748KB，整份加载又远超官方建议的 200 行。

做法：AGENTS.md 第 1–61 行（运维、安全、开发工作流约束，每次必守）原样移到 `.claude/rules/project.md`；其余（蒸馏管线、缺陷台账、验证纪律）留在 AGENTS.md，不自动加载。

依据（code.claude.com，2026-10-17 线上版）：memory 页——`.claude/rules/` 下无 `paths:` 的规则每次加载；context-window 页——「unscoped rules | Re-injected from disk」；memory 页——`instructionFiles: "claude-md"` 只读 CLAUDE.md、不读 AGENTS.md。同类做法：thisisgm/flea #299（2026-10-12）。

## 1. 已查实的约束（S0 复核，不成立就停）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | AGENTS.md 第 1 行是 `# Character-distill 生产运维`，第 62 行是 `## 蒸馏管线`；1–61 行为运维、SSH、部署、镜像清理、备份、安全铁律、开发工作流约束 | `AGENTS.md:1`、`:62` |
| C2 | 仓库中「`AGENTS.md:行号`」引用共 23 处，全部在 `docs/specs/*.md`（各自基线的快照）、`docs/evidence/README.md:203` 和 `tests/test_evidence_integrity.py:65` 的语料字符串里；没有代码按行号定位 AGENTS.md。**这些引用不改** | `git grep -nE 'AGENTS\.md:[0-9]+'` |
| C3 | 测试只要求 AGENTS.md 是被跟踪的 .md | `tests/test_evidence_integrity.py:143` |
| C4 | `.gitignore` 已放行 `.claude/rules/` | `.gitignore:231-232`（#178） |

## 2. 改动

1. 新增 `.claude/rules/project.md`：内容为 AGENTS.md 第 1–61 行，逐字节照搬（用脚本截取，不手抄）。
2. AGENTS.md：删掉第 1–61 行，在开头写：
   ```
   # Character-distill 工程记录

   > 每次必守的运维、安全与开发约束已移到 `.claude/rules/project.md`（Claude Code 每次会话自动加载）。本文件是蒸馏管线说明、缺陷台账与验证纪律，按需查阅，不自动加载。
   ```
3. 不改其他文件，不加测试。

## 3. 验收

- A1 `git diff --stat` 只有这两个文件；`project.md` 与原 AGENTS.md 1–61 行逐字节一致（贴 `cmp` 结果）。
- A2 开 PR，两项必需检查绿（重点看 `test_evidence_integrity`、`test_ledger_substitution_markers`）。
- A3（合并后，Shiyu 本机）见提示词第二段：备份并删本地 CLAUDE.md；用户级设置 `instructionFiles: "claude-md"`；`project.md` 复制到 bare 主目录；全新会话问「跑测试前要先做什么」→「先起测试库」，问「本地要不要跑全量」→「不跑，全量只在 CI」；`/context` 里有 `project.md`，没有 AGENTS.md。

## 补充（执行与审计发现写这里）

### 2026-10-18 审计补充

- PR #180 合并前追加了一处 spec 未列的改动：`.claude/rules/principles.md` 第 3 行把「已写在 AGENTS.md「开发工作流约束」里的」改为「已写在 .claude/rules/project.md 里的」（Shiyu 同意，同一件事：规则写在哪）。§2「不改其他文件」与 A1「只有这两个文件」据此作废。
- §2 第 2 项给的开头文本漏了一个空行：`AGENTS.md:3` 的 `>` 行与 `:4` 的 `## 蒸馏管线` 之间应有空行。在收尾 PR 中补上。
- C2 不准确：23 处行号引用里有 2 处在 AGENTS.md 自身（:273 样例字符串、:1421 历史记录），不改，结论不变。
