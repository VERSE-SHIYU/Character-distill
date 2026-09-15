# -*- coding: utf-8 -*-
"""缺陷 41 A 轮变异矩阵驱动 —— 一组共 13 条
（A-1、A-2、A-3、A-4、A-5、A-6、A-7、A-8、A-9、A-10、A-11、A-12、A-13）。

**A-8～A-13 是锁改成封闭语法之后补的**（返工）：前七条打的是**必需成分在不在**，后六条打的是
**多余成分在不在** —— 两个编排文件同步改同一处，其中四种改法（A-8/A-9/A-10/A-11）曾把
**开放式**的第一版锁整条绕过（见 `tests/test_pg_healthcheck.py` 的 docstring）。它们全部
落在同一个文件对（prod + local）上，故每条都写两条 `repl` 动作，各改一份。

**为什么入库。** A 轮的 commit 引用了本脚本跑出来的「哪条红、红在哪句」。数字骑在仓外
脚本上追不回来（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**复用而不是复制。** `_apply` / `_restore` / `_run` 三个执行原语直接从
`tests/perf/route_facts_mutations.py` import —— 锚点「恰一命中」的断言、逐字节还原、
子进程 pytest 取红源，三件事各只有一份实现。本文件只补自己那部分：A 组的变异表、
先验基线门、计数门。**不复制那三个函数的本体**，否则两份实现会各自漂移。

**不变量（与另外两个驱动同一套）**
  1. **先验基线**：锁全绿才开跑。基线不绿时「变异后红」说不清红源。
  2. **锚点恰一命中**：由 `_apply` 断言，0 命中或 2 命中当场拒跑 —— 锚点漂移会静默变成
     「变异没生效」，那是最危险的假绿。
  3. **还原逐字节**：收尾核 sha256。

**跑矩阵期间不要编辑任何靶子文件**：`_restore` 会把它们在开跑那一刻的字节写回，
期间的编辑会被静默覆盖（这条已记进 AGENTS.md 缺陷 41 的记账）。

**用法**
    python tests/perf/pg_healthcheck_mutations.py            # 全部 7 条
    python tests/perf/pg_healthcheck_mutations.py --group A  # 同上（本驱动只有 A 组）
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PERF_DIR = pathlib.Path(__file__).resolve().parent
ROOT = PERF_DIR.parents[1]
sys.path.insert(0, str(PERF_DIR))

import route_facts_mutations as framework  # noqa: E402  —— 执行框架的唯一一份实现

PROD = ROOT / "docker-compose.prod.yml"
LOCAL = ROOT / "docker-compose.local.yml"
LOCK = ROOT / "tests" / "test_pg_healthcheck.py"

TARGETS = (PROD, LOCAL, LOCK)

LOCK_PATH = "tests/test_pg_healthcheck.py"

# 仓里那条探针命令行（在**每个**编排文件里各出现一次）。写成常量而不是逐条重抄：
# 锚点是与它逐字比较的，抄错一个转义就变成「0 命中」，而 0 命中由 `_apply` 当场拦下。
_PSQL_LINE = (
    "      test: [\"CMD-SHELL\", \"PGPASSWORD=\\\"$$POSTGRES_PASSWORD\\\" "
    "PGCONNECT_TIMEOUT=3 psql -h postgres -U \\\"$$POSTGRES_USER\\\" "
    "-d \\\"$$POSTGRES_DB\\\" -tAc 'select 1' > /dev/null\"]"
)
# prod 那份的 `-h` 只在 psql 调用里出现一次；local 那份同理，故两条各自锚在 `-h` 附近，
# 不去动整行 —— 整行锚会在「同一行里改两处」时说不清是哪一处生效的。
_SZ_TAIL = "PGCONNECT_TIMEOUT=3 psql -h postgres -U"

# A-8～A-13 的锚（每个在两个编排文件里各恰好命中一次，故每条变异对两份各下一处同形改动）。
_PW_PREFIX = '"CMD-SHELL", "PGPASSWORD='          # A-8：在它前面插一个赋值
_PSQL_H = "psql -h postgres -U"                   # A-9 / A-13：在它附近加东西
_QUERY_TAIL = "'select 1' > /dev/null\"]"         # A-10 / A-11：在它后面接第二条命令
_REDIRECT_TAIL = "> /dev/null\"]"                 # A-12：把 stdout 那份重定向也吞掉 stderr


def _both(old: str, new: str) -> list[tuple]:
    """同一处改动同时落在两个编排文件上 —— 一份改一份不改会先红在「多份不一致」那条上，
    红源就不是本条要打的那句了。"""
    return [("repl", PROD, [(old, new)]), ("repl", LOCAL, [(old, new)])]

# 每条 = (编号 + 命题, 靶子测试, [(动作, 文件, 载荷)], 期望, 红源标记)
A_GROUP = [
    ("A-1  prod 改回 pg_isready",
     LOCK_PATH,
     [("repl", PROD, [(_PSQL_LINE,
                       '      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]')])],
     "RED", "pg_isready"),

    ("A-2  local 的 -h postgres 改为 -h 127.0.0.1",
     LOCK_PATH,
     [("repl", LOCAL, [(_SZ_TAIL,
                        "PGCONNECT_TIMEOUT=3 psql -h 127.0.0.1 -U")])],
     "RED", "环回"),

    ("A-3  删除 local 的 -h",
     LOCK_PATH,
     [("repl", LOCAL, [(_SZ_TAIL,
                        "PGCONNECT_TIMEOUT=3 psql -U")])],
     "RED", "省略了 -h"),

    ("A-4  prod 的密码改为 compose 插值 ${POSTGRES_PASSWORD}",
     LOCK_PATH,
     [("repl", PROD, [('PGPASSWORD=\\"$$POSTGRES_PASSWORD\\"',
                       'PGPASSWORD=\\"${POSTGRES_PASSWORD}\\"')])],
     "RED", "宿主机展开"),

    ("A-5  只改 local 一份的 PGCONNECT_TIMEOUT",
     LOCK_PATH,
     [("repl", LOCAL, [(_SZ_TAIL, _SZ_TAIL.replace("TIMEOUT=3", "TIMEOUT=4"))])],
     "RED", "多份不一致"),

    ("A-6  local 的服务键名 postgres 改为 db（healthcheck 仍写 -h postgres）",
     LOCK_PATH,
     [("repl", LOCAL, [("\n  postgres:\n", "\n  db:\n")])],
     "RED", "键名"),

    ("A-7  锁的 YAML 解析恒返回空",
     LOCK_PATH,
     [("repl", LOCK, [("    out: dict[str, dict] = {}\n    for path in sorted",
                       "    return {}\n    for path in sorted")])],
     "RED", "解析器瞎了"),

    # ── A-8～A-13：开放式检查看不见的第二类改法 —— 多余成分 ────────────────────
    # 六条的期望都不是「某个必需成分没了」，而是「多了一个语法外的 token」。
    ("A-8  两个文件都加 PGHOSTADDR=127.0.0.1",
     LOCK_PATH,
     _both(_PW_PREFIX, '"CMD-SHELL", "PGHOSTADDR=127.0.0.1 PGPASSWORD='),
     "RED", "PGHOSTADDR"),

    ("A-9  两个文件都在 -h postgres 后加 --host=127.0.0.1",
     LOCK_PATH,
     _both(_PSQL_H, "psql -h postgres --host=127.0.0.1 -U"),
     "RED", "--host"),

    ("A-10 两个文件都在末尾加 ; exit 0",
     LOCK_PATH,
     _both(_QUERY_TAIL, "'select 1' > /dev/null; exit 0\"]"),
     "RED", "';'"),

    ("A-11 两个文件都在末尾加 || true",
     LOCK_PATH,
     _both(_QUERY_TAIL, "'select 1' > /dev/null || true\"]"),
     "RED", "'||'"),

    ("A-12 两个文件都把结尾改成 > /dev/null 2>/dev/null",
     LOCK_PATH,
     _both(_REDIRECT_TAIL, "> /dev/null 2>/dev/null\"]"),
     "RED", "'2>'"),

    ("A-13 两个文件都把 -h postgres 写两遍",
     LOCK_PATH,
     _both(_PSQL_H, "psql -h postgres -h postgres -U"),
     "RED", "-h 出现了两次"),
]

GROUPS = {"A": A_GROUP}

_DOC_STAT = re.compile(r"一组共\s*(\d+)\s*条\s*[（(]([^）)]*)[)）]")
_ID_RE = re.compile(r"A-\d+")


def _live_ids() -> set[str]:
    """变异表里现存的编号集合。整表唯一的编号来源，也是下面自检的被比较项。"""
    out: set[str] = set()
    for items in GROUPS.values():
        for item in items:
            found = _ID_RE.search(item[0])
            assert found, f"变异条目没有编号：{item[0]!r}"
            out.add(found.group(0))
    return out


def _count_gate() -> bool:
    """docstring 声明的**编号集合**必须等于变异表的现数；匹配不到同样拒跑。

    比集合而不是比总数：总数对上、编号对不上同样是漂移，而总数相等时那种漂移正好藏得住。
    自检失效与自检通过长得一样，故匹配不到也拒跑。
    """
    m = _DOC_STAT.search(__doc__ or "")
    declared = set(_ID_RE.findall(m.group(2))) if m else None
    stated_total = int(m.group(1)) if m else None
    live = _live_ids()
    if declared is not None and stated_total == len(live) and declared == live:
        return True
    print("\ndocstring 的编号与变异表的现数不一致，拒绝跑（自检失效与自检通过长得一样）：\n"
          f"  文档：共 {stated_total} 条 "
          f"{sorted(declared) if declared is not None else '（条数/括号匹配不到）'}\n"
          f"  现数：共 {len(live)} 条 {sorted(live)}")
    return False


def _baseline_gate() -> list[str]:
    """先验基线：锁全绿才开跑 —— 否则「变异后红」说不清红源。"""
    bad = []
    for target in (LOCK_PATH,):
        summary, _ = framework._run(target)
        print(f"  基线 {target:38s} {summary}")
        if "failed" in summary or "error" in summary:
            bad.append(target)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="A", help="要跑的组（本驱动只有 A 组）")
    args = ap.parse_args()

    if not _count_gate():
        return 2

    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    if _baseline_gate():
        print("\n基线不绿 —— 拒绝跑变异矩阵（红源说不清）。先修基线。")
        return 2

    mismatches: list[str] = []
    wanted = [x.strip() for x in args.group.upper().split(",") if x.strip()]
    for name in [g for g in wanted if g in GROUPS]:
        print(f"\n===== {name} 组 =====")
        for item in GROUPS[name]:
            label, target, edits, expect = item[:4]
            marker = item[4] if len(item) > 4 else None
            framework._apply(edits)
            summary, keep = framework._run(target)
            got = "RED" if ("failed" in summary or "error" in summary) else "green"
            framework._restore(baseline)
            if got != expect:
                mismatches.append(f"{label}：期望 {expect} 实得 {got}")
            if marker and not any(marker in k for k in keep):
                mismatches.append(f"{label}：红源里没有 {marker!r}（红的不是那条断言）")
            print(f"\n### {label}   期望={expect}  实得={got}")
            for k in keep:
                print("   ", k)
            print("   >>", summary)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        same = got == hashlib.sha256(baseline[p]).hexdigest()
        if not same:
            mismatches.append(f"{p.name} 还原后 sha256 不符")
        print(f"  {str(p.relative_to(ROOT)):38s} {same}  {got[:16]}")

    print("\n== 结论 ==")
    if mismatches:
        for m in mismatches:
            print("  MISMATCH", m)
        return 1
    print("  全部符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
