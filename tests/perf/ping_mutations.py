# -*- coding: utf-8 -*-
"""缺陷 41（B 轮）变异矩阵驱动 —— 一组共 9 条（B1/B2/B3/B4/B5/B6/B7/B8/B9）。

**为什么入库。** B 轮四条 commit 的判据引用本脚本跑出来的「哪条红、红在哪句」。
数字骑在仓外脚本上追不回来（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**复用而不是复制。** `_apply` / `_restore` / `_run` 三个执行原语直接从
`tests/perf/route_facts_mutations.py` import —— 锚点「恰一命中」的断言、逐字节还原、
子进程 pytest 取红源，三件事各只有一份实现。本文件只补自己那部分：B 组的变异表、
先验基线门、计数门。**不复制那三个函数的本体**，否则两份实现会各自漂移
（与缺陷 42 三处收口同一个病）。

**不变量（与 `route_facts_mutations.py` 同一套）**
  1. **先验基线**：三把新锁全绿才开跑。基线不绿时「变异后红」说不清红源。
  2. **锚点恰一命中**：由 `_apply` 断言，0 命中或 2 命中当场拒跑。
  3. **还原逐字节**：收尾核对 sha256。

**用法**
    python tests/perf/ping_mutations.py                 # 全部 9 条
    python tests/perf/ping_mutations.py --group B1B2    # 只跑前两条

九条覆盖三层：B-1/B-2 打存储层契约（`storage/`），B-3/B-4/B-5 打就绪端点（`web/server.py`），
B-6～B-9 打三处探针目标的锁（`Dockerfile` / `deploy.yml` / 锁自己）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
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

SQLITE = ROOT / "storage" / "sqlite_store.py"
BASE = ROOT / "storage" / "base.py"
SERVER = ROOT / "web" / "server.py"
DOCKERFILE = ROOT / "Dockerfile"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
LOCK = ROOT / "tests" / "test_health_probe_targets.py"

TARGETS = (SQLITE, BASE, SERVER, DOCKERFILE, DEPLOY, LOCK)

PING_LOCK = "tests/test_storage_ping.py"
READY_LOCK = "tests/test_health_ready.py"
TARGETS_LOCK = "tests/test_health_probe_targets.py"

_SZ_BLOCKS = '''            # 第一段：存活。/api/health 不碰库，只说明进程起来了。
            if ! SZ_gate "liveness" "/api/health" "app 未起来（/api/health 不通），回滚"; then
              SZ_rollback
              exit 1
            fi

            # 第二段：就绪。真查一次库 —— 凭据错、库连不上在这里才第一次可见。
            if ! SZ_gate "readiness" "/api/health/ready" \\
                 "app 已起来但数据库不可用（凭据或网络）；回滚不一定能修复配置层故障"; then
              SZ_rollback
              exit 1
            fi
'''

_SG_BLOCKS = _SZ_BLOCKS.replace("SZ", "SG")


def _swapped(blocks: str) -> str:
    """把两段对调（只对调 `if ! X_gate ...` 那两块，注释跟着块走）。"""
    liveness, readiness = blocks.split("\n\n")
    return readiness + "\n\n" + liveness + "\n"


_ORDER_MUTATION = [
    (_SZ_BLOCKS, _swapped(_SZ_BLOCKS)),
    (_SG_BLOCKS, _swapped(_SG_BLOCKS)),
]

# 每条 = (编号 + 命题, 靶子测试, [(动作, 文件, 载荷)], 期望, 红源标记)
B_GROUP = [
    ("B-1  SQLite 的 ping 只取连接、不执行语句",
     PING_LOCK,
     [("repl", SQLITE, [('        async with await self._connect() as conn:\n'
                         '            await conn.execute("SELECT 1")\n',
                         '        async with await self._connect() as conn:\n'
                         '            pass\n')])],
     "RED", "test_sqlite_ping_raises_when_locked_out_mid_flight"),

    ("B-2  摘掉 StorageBase.ping 上的 @abstractmethod",
     PING_LOCK,
     [("repl", BASE, [("    @abstractmethod\n    async def ping(self) -> None:",
                       "    async def ping(self) -> None:")])],
     "RED", "test_storage_base_subclass_without_ping_cannot_be_instantiated"),

    ("B-3  就绪端点从 PUBLIC_PATHS 里摘掉",
     READY_LOCK,
     [("repl", SERVER, [('"/api/auth/reset-password", "/api/health", "/api/health/ready",',
                         '"/api/auth/reset-password", "/api/health",')])],
     "RED", "公开路径被认证中间件拦下"),

    ("B-4  503 响应体带上 str(exc)",
     READY_LOCK,
     [("repl", SERVER, [('        return JSONResponse({"status": "unready"}, status_code=503)',
                         '        return JSONResponse({"status": "unready", "detail": str(exc)},'
                         ' status_code=503)')])],
     "RED", "漏出了这些串"),

    ("B-5  就绪端点不调 ping、直接回 ready",
     READY_LOCK,
     [("repl", SERVER, [('    try:\n        await storage.ping()\n    except Exception as exc:',
                         '    try:\n        pass\n    except Exception as exc:')])],
     "RED", "assert 200 == 503"),

    ("B-6  Dockerfile 的 HEALTHCHECK 换回存活路径",
     TARGETS_LOCK,
     [("repl", DOCKERFILE, [("urlopen('http://localhost:7860/api/health/ready')",
                             "urlopen('http://localhost:7860/api/health')")])],
     "RED", "没探就绪路径"),

    ("B-7  deploy.yml 删掉 SG 的第二段",
     TARGETS_LOCK,
     [("repl", DEPLOY, [('''            # 第二段：就绪。真查一次库 —— 凭据错、库连不上在这里才第一次可见。
            if ! SG_gate "readiness" "/api/health/ready" \\
                 "app 已起来但数据库不可用（凭据或网络）；回滚不一定能修复配置层故障"; then
              SG_rollback
              exit 1
            fi
''', '''            # （变异：SG 的第二段被删掉）
''')])],
     "RED", "deploy-sg"),

    ("B-8  deploy.yml 两段顺序颠倒",
     TARGETS_LOCK,
     [("repl", DEPLOY, _ORDER_MUTATION)],
     "RED", "把两段顺序弄反了"),

    ("B-9  锁自己的解析函数恒返回空",
     TARGETS_LOCK,
     [("repl", LOCK, [("    out: set[str] = set()", "    return set()")])],
     "RED", "解析器瞎了"),
]

GROUPS = {"B": B_GROUP}

_DOC_STAT = re.compile(r"一组共\s*(\d+)\s*条\s*[（(]([^）)]*)[)）]")
_DOC_GROUP = re.compile(r"([A-Z])(\d+)")


def _count_gate() -> bool:
    """文档字符串里的条数必须等于分组的现数；匹配不到同样拒跑（自检失效与通过同形）。"""
    live = {g: len(v) for g, v in GROUPS.items()}
    m = _DOC_STAT.search(__doc__ or "")
    stated_total = int(m.group(1)) if m else None
    stated = {g: int(n) for g, n in _DOC_GROUP.findall(m.group(2))} if m else {}
    if stated_total == sum(live.values()) and stated == live:
        return True
    print("\n文档字符串的条数与分组的现数不一致，拒绝跑（自检失效与自检通过长得一样）：\n"
          f"  文档：共 {stated_total} 条 {stated or '（分组的括号匹配不到）'}\n"
          f"  现数：共 {sum(live.values())} 条 {live}")
    return False


def _baseline_gate() -> list[str]:
    """先验基线：三把新锁全绿才开跑 —— 否则「变异后红」说不清红源。"""
    bad = []
    for target in (PING_LOCK, READY_LOCK, TARGETS_LOCK):
        summary, _ = framework._run(target)
        print(f"  基线 {target:38s} {summary}")
        if "failed" in summary or "error" in summary:
            bad.append(target)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="B",
                    help="要跑的组（本驱动只有 B 组；默认全部 9 条）")
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
