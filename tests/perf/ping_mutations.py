# -*- coding: utf-8 -*-
"""缺陷 41（B 轮）变异矩阵驱动 —— 一组共 15 条
（B-1、B-1b、B-2、B-3、B-4、B-5、B-6、B-7、B-8、B-9、B-10、B-11、B-12、B-13、B-14）。

**为什么入库。** B 轮四条 commit 的判据引用本脚本跑出来的「哪条红、红在哪句」。
数字骑在仓外脚本上追不回来（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**复用而不是复制。** `_apply` / `_restore` / `_run` 三个执行原语直接从
`tests/perf/route_facts_mutations.py` import —— 锚点「恰一命中」的断言、逐字节还原、
子进程 pytest 取红源，三件事各只有一份实现。本文件只补自己那部分：B 组的变异表、
先验基线门、计数门。**不复制那三个函数的本体**，否则两份实现会各自漂移
（与缺陷 42 三处收口同一个病）。

**不变量（与 `route_facts_mutations.py` 同一套）**
  1. **先验基线**：三把新锁既不红、也跑得起来才开跑。基线红或跑不起来时「变异后红」说不清
     红源（判据没执行 ≠ 通过，见 `lock_coverage.outcome`）；skip 不算坏。
  2. **锚点恰一命中**：由 `_apply` 断言，0 命中或 2 命中当场拒跑。
  3. **还原逐字节**：收尾核对 sha256。

**跑完写产物**：`tests/perf/ping_red_lines.json`（覆盖域 + 每条变异红在**变异前**坐标系的哪一行）。
本驱动原先不写产物 —— 于是它是一把**没有被元锁覆盖的锁**：`test_lock_coverage.py` 判「驱动 ↔
产物」对不上（`ping_mutations.py` 有驱动没产物），缺口在那里响过一次，而修法只有一种：把产物补上，
让它进覆盖域。产物里的 `skipped` 只收登记过的「本环境不适用」条目，那些条目从 `mutations` 里摘掉 ——
一条判别器都没撞到的条目留在里面会被记成「空转变异」，而它在**这台机器上**红绿空转都无从谈起。

**用法**
    python tests/perf/ping_mutations.py                 # 全部 15 条
    python tests/perf/ping_mutations.py --group B1B2    # 只跑前两条

**B-1 / B-1b 在 Windows 上是「本环境不适用」，不是绿也不是红。** 这两条打的是同一处
改动（`storage/sqlite_store.py` 的 ping）、由同一条用例判定，而本机 sqlite 3.49.1 对任何
语句都判锁，`test_sqlite_ping_raises_when_locked_out_mid_flight` 的前提自检因此在变异
生效前就 `pytest.skip`。驱动把 skip 单列成一档（见 `_MAY_SKIP` / `lock_coverage.outcome`）—— 记成
green 是假绿（§四：判据在空转），记成 RED 又冤枉了变异。只有登记过的条目可以 skip，
别的条目 skip 一律记 mismatch，否则锁在悄悄跳过也看不出来。**Linux/sqlite 3.46 上这两条
才是真正的 RED**，Windows 的结论不构成证据。

十五条覆盖三层：B-1/B-1b/B-2 打存储层契约（`storage/`），B-3/B-4/B-5/B-13 打就绪端点
（`web/server.py`），B-6～B-12/B-14 打三处探针目标的锁（`Dockerfile` / `deploy.yml` / 锁自己）。

**B-1b 与 B-13 各自与 B-1、B-5 用的是同一处改动**：不是重复，是同一个洞在两个平台上、或
两把锁上各有一条判别面 —— 哪一把先红、红在哪句，正是要留档的东西。
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
sys.path.insert(0, str(ROOT / "tests"))

import lock_coverage  # noqa: E402  —— 判档与产物写入的唯一一份实现（三个驱动共用）
import route_facts_mutations as framework  # noqa: E402  —— 执行框架的唯一一份实现

SQLITE = ROOT / "storage" / "sqlite_store.py"
BASE = ROOT / "storage" / "base.py"
SERVER = ROOT / "web" / "server.py"
DOCKERFILE = ROOT / "Dockerfile"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
LOCK = ROOT / "tests" / "test_health_probe_targets.py"

TARGETS = (SQLITE, BASE, SERVER, DOCKERFILE, DEPLOY, LOCK)
ARTIFACT = PERF_DIR / "ping_red_lines.json"

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

# B-14 的载荷：插在 deploy-sg 之前的第三个区域，gate 定义齐全但只被调用一次。
# 缩进对齐真实 job（job 键 2 空格、run 块内容 12 空格），故锁的解析器按同一条规矩看得见它。
_XX_JOB = (
    "\n"
    "  deploy-xx:\n"
    "    needs: [resolve-digest]\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Probe\n"
    "        run: |\n"
    "            XX_gate() {\n"
    '              XX_stage="$1"; XX_path="$2"; XX_why="$3"\n'
    '              curl -sf --max-time 5 "http://localhost:7860${XX_path}"\n'
    "            }\n"
    '            XX_gate "readiness" "/api/health/ready"\n'
)

# 每条 = (编号 + 命题, 靶子测试, [(动作, 文件, 载荷)], 期望, 红源标记)
B_GROUP = [
    ("B-1  SQLite 的 ping 只取连接、不执行语句",
     PING_LOCK,
     [("repl", SQLITE, [('        async with await self._connect() as conn:\n'
                         '            await conn.execute("SELECT count(*) FROM sqlite_master")\n',
                         '        async with await self._connect() as conn:\n'
                         '            pass\n')])],
     "RED", "test_sqlite_ping_raises_when_locked_out_mid_flight"),

    ("B-1b  SQLite 的 ping 语句退回 SELECT 1",
     PING_LOCK,
     [("repl", SQLITE, [('            await conn.execute("SELECT count(*) FROM sqlite_master")',
                         '            await conn.execute("SELECT 1")')])],
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
     "RED", "在所有需要它红的场合都是绿的"),

    ("B-7  deploy.yml 删掉 SG 的第二段",
     TARGETS_LOCK,
     [("repl", DEPLOY, [('''            # 第二段：就绪。真查一次库 —— 凭据错、库连不上在这里才第一次可见。
            if ! SG_gate "readiness" "/api/health/ready" \\
                 "app 已起来但数据库不可用（凭据或网络）；回滚不一定能修复配置层故障"; then
              SG_rollback
              exit 1
            fi
''', '''            # （变异：SG 的第二次 gate 调用被删掉）
''')])],
     "RED", "两段门至少要两次调用"),

    ("B-8  deploy.yml 两段顺序颠倒",
     TARGETS_LOCK,
     [("repl", DEPLOY, _ORDER_MUTATION)],
     "RED", "第一段是存活检查，不该依赖库"),

    ("B-9  锁自己的解析函数恒返回空",
     TARGETS_LOCK,
     [("repl", LOCK, [("    out: set[str] = set()", "    return set()")])],
     "RED", "解析器瞎了"),

    ("B-10 删掉 SG 存活检查的调用行（注释与文案保留）",
     TARGETS_LOCK,
     [("repl", DEPLOY, [('''            # 第一段：存活。/api/health 不碰库，只说明进程起来了。
            if ! SG_gate "liveness" "/api/health" "app 未起来（/api/health 不通），回滚"; then
              SG_rollback
              exit 1
            fi
''', '''            # 第一段：存活。/api/health 不碰库，只说明进程起来了。
            # （变异：SG 的存活检查调用行被删掉，注释与失败文案原样留着）
''')])],
     "RED", "两段门至少要两次调用"),

    ("B-11 SG 的存活检查改探就绪路径",
     TARGETS_LOCK,
     [("repl", DEPLOY, [('SG_gate "liveness" "/api/health" "app 未起来（/api/health 不通），回滚"',
                         'SG_gate "liveness" "/api/health/ready" "app 未起来（/api/health 不通），回滚"')])],
     "RED", "第一段是存活检查，不该依赖库"),

    ("B-12 两个区域的 gate 都把 curl 写成固定路径",
     TARGETS_LOCK,
     [("repl", DEPLOY, [
         ('curl -sf --max-time 5 "http://localhost:7860${SZ_path}"',
          'curl -sf --max-time 5 "http://localhost:7860/api/health"'),
         ('curl -sf --max-time 5 "http://localhost:7860${SG_path}"',
          'curl -sf --max-time 5 "http://localhost:7860/api/health"'),
     ])],
     "RED", "写死的路径"),

    ("B-13 就绪端点不调 ping、直接回 ready（本锁红）",
     TARGETS_LOCK,
     [("repl", SERVER, [('    try:\n        await storage.ping()\n    except Exception as exc:',
                         '    try:\n        pass\n    except Exception as exc:')])],
     "RED", "在所有需要它红的场合都是绿的"),

    ("B-14 新增一个只调用一次 gate 的 deploy-xx",
     TARGETS_LOCK,
     [("repl", DEPLOY, [("\n  deploy-sg:", _XX_JOB + "\n  deploy-sg:")])],
     "RED", "两段门至少要两次调用"),
]

GROUPS = {"B": B_GROUP}

_DOC_STAT = re.compile(r"一组共\s*(\d+)\s*条\s*[（(]([^）)]*)[)）]")
# 编号形如 B-1 / B-1b —— 后缀小写字母是同一命题的第二形态（B-1b 是 B-1 的近邻变体）。
_ID_RE = re.compile(r"B-\d+[a-z]?")

# 允许 skip 的条目：判据在本环境前提不成立（见模块 docstring 的 B-1/B-1b 段）。
# 两条都登记，是因为它们打的是**同一处**改动、由**同一条**用例判定：前提自检在前，
# 变异还没轮上就被 skip 了。不登记的那一条会记 mismatch，那是这个门存在的意义 ——
# 别的条目一旦被 skip，说明锁在悄悄跳过，而假绿与真绿长得一样。
_MAY_SKIP = {"B-1", "B-1b"}

# 汇总行的判档（RED / green / 本环境不适用 / 跑不出来）由 lock_coverage.outcome 一处实现 ——
# 三个驱动共用同一份，免得各自漂移（本文件原先自己抄了一份三档版，缺「跑不出来」）。


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

    比集合而不是比总数：总数对上、编号对不上（漏了 B-1b、多了个不存在的）同样是漂移，
    而总数相等时那种漂移正好藏得住。自检失效与自检通过长得一样，故匹配不到也拒跑。
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
    """先验基线：三把新锁既不红、也跑得起来才开跑 —— 否则「变异后红」说不清红源。

    skip 不算坏（见 `lock_coverage.baseline_ok`）：`test_storage_ping.py` 基线上就有两条
    前提 skip（B-1/B-1b 的锁版 sqlite）。
    """
    bad = []
    for target in (PING_LOCK, READY_LOCK, TARGETS_LOCK):
        summary, _, _, _ = framework._run(target)
        print(f"  基线 {target:38s} {summary}")
        if not lock_coverage.baseline_ok(summary):
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
    domain = lock_coverage.domain_of(GROUPS)
    hits: dict[str, list[str]] = {}
    skipped: list[str] = []
    wanted = [x.strip() for x in args.group.upper().split(",") if x.strip()]
    for name in [g for g in wanted if g in GROUPS]:
        print(f"\n===== {name} 组 =====")
        for item in GROUPS[name]:
            label, target, edits, expect = item[:4]
            marker = item[4] if len(item) > 4 else None
            framework._apply(edits)
            summary, keep, lines, problems = framework._run(target)
            got = lock_coverage.outcome(summary)
            framework._restore(baseline)
            # 只留落在覆盖域里的行：本驱动的靶子就是那个锁文件，别处（stdlib / 被测模块）的帧不算。
            hits[label] = sorted(l for l in lines if l.rsplit(":", 1)[0] in set(domain))
            if problems:
                mismatches.append(f"{label}：{'；'.join(problems)}")
            if got == lock_coverage.RUNAWAY:
                # 跑不起来与绿必须分开：判据根本没执行时，「绿」是把没跑当成通过。
                mismatches.append(
                    f"{label}：{lock_coverage.RUNAWAY} —— 判据没跑起来，这条变异无法验证")
                print(f"\n### {label}   期望={expect}  实得={got}（不计红绿）")
                for k in keep:
                    print("   ", k)
                print("   >>", summary)
                continue
            if got == "本环境不适用":
                ident = _ID_RE.search(label).group(0)
                if ident not in _MAY_SKIP:
                    mismatches.append(
                        f"{label}：判据被 skip 了（该编号未登记为「本环境不适用」）")
                # 走了 skip 的条目从覆盖域里摘出去：它一条判别器都没撞到，留着会被元锁
                # 记成「空转变异」—— 而它红/绿/空转在这台机器上根本无从谈起（见模块 docstring）。
                hits.pop(label, None)
                skipped.append(label)
                print(f"\n### {label}   期望={expect}  实得={got}（不计红绿）")
                print("   >>", summary)
                continue
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
        print("  矩阵有 mismatch —— 产物**不写**（写下去等于把没核对过的红源入库）。")
        return 1
    lock_coverage.write_artifact(ARTIFACT, "tests/perf/ping_mutations.py",
                                 domain, hits, skipped)
    print(f"  全部符合预期。产物已写：{ARTIFACT.relative_to(ROOT).as_posix()}")
    print("  （覆盖闭合由 tests/test_lock_coverage.py 核：判别器集合 == 被撞集合）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
