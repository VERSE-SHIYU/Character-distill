"""spec-119 读数普查：本批**新增**的失败记录调用，按「发不发告警邮件」分档。

**为什么入库**：台账 119 与交付报告里「删掉 130 行 `print(`、新增 129 个失败记录调用、
按级别 ERROR 45 / WARNING 84」是文档引用的数字 —— 按 §四「文档引用的数字，其产数脚本与
原始产物也要入库」，产出它们的这一份必须进仓，且支持 `--end` 回放历史版本：

    python tests/perf/alerting_level_census.py                    # 1b09328..HEAD
    python tests/perf/alerting_level_census.py --end 9015b8b      # 只看前三个 commit 的量

**为什么只数 diff 里的新增行**：本条的命题是「这一改动了哪些落点」，不是「全仓今天有多少
print」—— 后者会把改动前就存在的调用算进来。多行调用不影响计数：每个调用只有一行带
`logger.error(` / `logger.warning(` / `nonfatal(`。

**级别怎么定**（与 `AGENTS.md`「开发工作流约束」末条同一口径）：
  - `logger.error(...)` → ERROR（发告警邮件）
  - `logger.warning(...)` → WARNING（只上面板）
  - `nonfatal(...)` → 看**同一个调用**里有没有 `level=logging.WARNING`（跨行写法，故在
    `-U6` 的窗口里看紧随的几行）；没有就是默认档 ERROR。

**三处排除，都写在判据里而不是靠人记得**：
  - `tests/`：本条数的是**生产落点**；测试里新增的 `caplog` 断言不是落点。
  - `core/nonfatal.py`：它的新增行里有 `async def nonfatal(`（定义，不是调用）与
    `logger.log(level, ...)`，两者都会被 `\\bnonfatal\\(` 误收。该文件里今天没有
    `nonfatal(...)` 调用，故整文件排除不会漏计。
  - 非 `.py`：`docs/` / `.md` 里出现的字样是说明，不是调用点。
"""
from __future__ import annotations

import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "1b09328"
EXCLUDE_PREFIX = ("tests/",)
EXCLUDE_FILES = ("core/nonfatal.py",)
_CALL = re.compile(r"\b(logger\.error|logger\.warning|nonfatal)\(")
_WARN_LEVEL = re.compile(r"level\s*=\s*logging\.WARNING")
# `nonfatal(...)` 的 `level=` 可能写在**同一行**（`core/distiller.py:1438` 就是单行写全的），
# 也可能写在后面几行（本仓 `web/cross_border_sync.py` 的三处就是跨行的）—— 故窗口取
# 「本行 + 紧随 WINDOW 行」。**只往后看不含本行会漏掉单行写法**（实测：漏掉 distiller 那处，
# 分档就变成 ERROR 46 / WARNING 83，与台账的 45 / 84 差一处）。
WINDOW = 5


def added_calls(end: str):
    """产出 (仓内相对 posix 路径, 该新增行正文, 紧随其后的新增行窗口)。"""
    out = subprocess.run(
        ["git", "diff", f"-U{WINDOW}", f"{BASE}..{end}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(f"git diff 失败：{out.stderr.strip()}")
    lines = out.stdout.splitlines()
    cur = None
    for i, ln in enumerate(lines):
        if ln.startswith("+++ b/"):
            cur = ln[6:]
            continue
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        window = "\n".join(x[1:] for x in lines[i:i + 1 + WINDOW]
                           if x.startswith("+") and not x.startswith("+++"))
        yield cur, ln[1:], window


def census(end: str) -> tuple[collections.Counter, collections.Counter]:
    """回（按 (文件, 档) 的计数, 按档的合计）。"""
    per_file: collections.Counter = collections.Counter()
    counts: collections.Counter = collections.Counter()
    for path, body, window in added_calls(end):
        if path is None or path.startswith(EXCLUDE_PREFIX) \
                or path in EXCLUDE_FILES or not path.endswith(".py"):
            continue
        m = _CALL.search(body)
        if not m:
            continue
        kind = m.group(1)
        if kind == "nonfatal":
            level = "nonfatal:" + ("WARNING" if _WARN_LEVEL.search(window) else "ERROR")
        else:
            level = "ERROR" if kind == "logger.error" else "WARNING"
        per_file[(path, level)] += 1
        counts[level] += 1
    return per_file, counts


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="spec-119 新增失败记录调用按级别普查")
    ap.add_argument("--end", default="HEAD", help="区间终点（默认 HEAD）")
    args = ap.parse_args()

    per_file, counts = census(args.end)
    print(f"区间 {BASE}..{args.end}（排除 {', '.join(EXCLUDE_PREFIX + EXCLUDE_FILES)}）")
    for (path, level), n in sorted(per_file.items()):
        print(f"  {path:44s} {level:16s} {n}")
    print("-" * 74)
    for level, n in sorted(counts.items()):
        print(f"  {'TOTAL':44s} {level:16s} {n}")

    error = counts["ERROR"] + counts["nonfatal:ERROR"]
    warning = counts["WARNING"] + counts["nonfatal:WARNING"]
    total = sum(counts.values())
    print(f"  合计 {total} 处；ERROR（发告警邮件）{error} 处、WARNING（只上面板）{warning} 处")
    # 自身核对：分档之和必须等于总数，否则上面几行里有一个是错的（同族错误各自漂移）。
    assert error + warning == total, f"分档之和 {error + warning} ≠ 总数 {total} —— 计数漂了"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
