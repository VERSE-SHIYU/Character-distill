# -*- coding: utf-8 -*-
"""spec-119 变异矩阵驱动 —— 14 条变异打 `tests/test_failure_alerting.py` 的 16 条判别器。

**为什么入库。** 本份交付报告里的「哪条变异红、红在哪句」全部来自这个脚本。数字若只骑在
仓外脚本上，三个月后复现不了（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**本份守的是什么。** 失败可见这条链上有两处落点：往上抛的错误由 `web/server.py` 的全局
处理器统一记录，被吞掉的错误由各落点自己记录。两处的判据都必须**装真的告警出口**才判得
准 —— 面板（`RingBufferHandler`）只收 WARNING+，告警邮件只收 ERROR，于是「记没记」与
「记成哪一档」必须分开断言。矩阵按这两处各自的分支逐条打。

**不变量（每条变异都成立，脚本自检，不是口头保证）**
  1. **先验基线**：靶子先跑一遍，绿（或本环境 skip）才开跑。基线本身就红时，变异后的红
     说不清红源（§四：两种成因共用一个信号 = 两种都没有守卫）。
  2. **锚点恰一命中**：`src.count(old) == 1`，0 命中或 2 命中都当场 assert —— 锚点漂移
     会静默变成「变异没生效」，那是最危险的假绿。
  3. **变异不落在覆盖域的靶子文件上**：本矩阵的靶子就是域（`tests/test_failure_alerting.py`）。
     往它里面插/删一行，它自己的判别器行号会整体平移，红源坐标当场作废 —— 故 `_apply`
     当场拒绝（`realign_hits` 那一套在这里根本用不上，因为根本不该发生）。
  4. **还原逐字节**：每条跑完立刻按字节还原，收尾核 sha256；被创建的文件（M9）收尾必须
     不在树里（否则下一次基线跑已经不是同一条基线了）。

**M9 为什么是「往扫描面里放一个语法坏的文件」而不是改生产代码。** 它撞的那条判别器
（`tests/test_failure_alerting.py:209` 的 `raise AssertionError`）守的不是某种生产坏法，
而是**扫描这个动作本身**：扫到解析不动的文件要当场炸，不能静默跳过 —— 静默跳过的后果是
判据在**空集**上通过（假绿），而假绿正是「两种成因共用一个信号」里更坏的那一半。要撞到
它只有一条路：让扫描面上真的出现一个解析不动的文件。故这条变异动的是**输入**（写一个
`core/_spec119_syntax_probe.py`，用完删除），不是被守对象。

**M11–M13 补的是「按结构判定」这层判据的分辨力。** 旧判据的三个漏洞各有能撞红的样本：
M11 往 `core/distiller.py` 的 `else:` 分支里插一句 print（不在 `except` 里，靠文本规则
命中；旧写法只读 `.body`，会把它静默跳过）；M12 把 `web/routers/distill.py` 那处改回
`print` 而保留后面的 `raise HTTPException(400, …)`（HTTPException 走不到全局处理器，
失败仍只落 stdout，旧判据当「必然抛出」放过）；M13 删掉 `core/alerting.py` 那唯一的豁免
print（相等比较要能在**豁免失效**时变红）。三条都不重复 M10 已覆盖的「`except` 里改回
`print`」。

**用法**
    python tests/perf/alerting_mutations.py            # 全部（基线 + 13 条）
    python tests/perf/alerting_mutations.py --list     # 只列变异，不跑（核对锚点用）
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

# 断言文案是中文，子进程与本进程都过一遍 UTF-8 —— 否则 Windows 控制台的 GBK 会在打印
# 「哪条红、红在哪句」时 UnicodeEncodeError 崩掉，而那正是本脚本唯一的产出。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[2]

TEST = ROOT / "tests" / "test_failure_alerting.py"      # 靶子 == 覆盖域
SERVER = ROOT / "web" / "server.py"                     # 全局异常处理器
NONFATAL = ROOT / "core" / "nonfatal.py"                # 「吞掉但留痕」的唯一定义
ALERTING = ROOT / "core" / "alerting.py"                # 告警邮件的门槛（ALERT_LEVEL）
CONTEXT = ROOT / "core" / "context_engine.py"           # 一处被改造过的吞错落点
PROBE = ROOT / "core" / "_spec119_syntax_probe.py"      # M9 临时创建的语法坏文件
DISTILLER = ROOT / "core" / "distiller.py"               # M11：else 分支（旧扫描器静默跳过）
WEB_DISTILL = ROOT / "web" / "routers" / "distill.py"    # M12：打印后抛 HTTPException

TARGETS = (TEST, SERVER, NONFATAL, ALERTING, CONTEXT,
           DISTILLER, WEB_DISTILL)                       # 需要按字节还原的文件
DOMAIN = ["tests/test_failure_alerting.py"]

sys.path.insert(0, str(ROOT / "tests"))
import lock_coverage  # noqa: E402  —— 帧解析与产物写入的唯一一份实现

ARTIFACT = pathlib.Path(__file__).resolve().parent / "alerting_red_lines.json"


# ── 变异体 ─────────────────────────────────────────────────────────────────
# 每条 = (命题, 靶子测试文件, [(动作, 文件, 载荷)], 期望, 必须在红源里出现的标记)
#   动作 repl : 载荷 = [(old, new), ...]，每个 old 必须恰一命中
#   动作 write: 载荷 = 整份文件文本（写一个当前不存在的文件 —— M9 唯一一处）
#   期望值    : RED / green

MUTATIONS = [
    # ── 全局异常处理器（web/server.py）────────────────────────────────────────
    ("M1 处理器退回 `traceback.print_exc()`：异常只落 stdout，面板与邮件都看不见",
     "tests/test_failure_alerting.py",
     [("repl", SERVER, [
         ('    logger.exception("%s %s 未捕获的异常", request.method, request.url.path)\n',
          '    import traceback\n    traceback.print_exc()\n'),
     ])],
     "RED", "带堆栈的 ERROR"),

    ("M2 500 响应体把异常原文端给前端（契约变了，且漏内部细节）",
     "tests/test_failure_alerting.py",
     [("repl", SERVER, [
         ('        content={"detail": "服务器内部错误，请稍后重试"},\n',
          '        content={"detail": str(exc)},\n'),
     ])],
     "RED", "返回给前端的响应变了"),

    ("M3 处理器不带请求方法与路径：留了痕，但排障只剩堆栈",
     "tests/test_failure_alerting.py",
     [("repl", SERVER, [
         ('    logger.exception("%s %s 未捕获的异常", request.method, request.url.path)',
          '    logger.exception("未捕获的异常")'),
     ])],
     "RED", "日志没说是哪个请求挂的"),

    # ── nonfatal 的级别口径（core/nonfatal.py）───────────────────────────────
    # 锚点都在 `_report` / 签名行这类**全仓唯一**的位置上。判据 2（锚点恰一命中）
    # 不是走过场：`except Exception as exc:` 那段在同步／异步两版里逐字相同，
    # 锚在它上面必然命中两处 —— 从前靠「只存在一版」侥幸过关，抽出 `_report`、
    # 补上同步版之后就不成立了，于是三条锚点全部改到唯一处。
    ("M4 nonfatal 忽略调用方给的 level，一律按 ERROR 记（兜底动作开始发邮件）",
     "tests/test_failure_alerting.py",
     [("repl", NONFATAL, [('    _logger.log(\n        level,',
                           '    _logger.log(\n        logging.ERROR,')])],
     "RED", "应恰有一条 WARNING"),

    ("M5 nonfatal 的默认档降到 WARNING（数据没存进去 / 请求失败不再发邮件）",
     "tests/test_failure_alerting.py",
     [("repl", NONFATAL, [
         ('async def nonfatal(\n    source: str, what: str, *, level: int = logging.ERROR,',
          'async def nonfatal(\n    source: str, what: str, *, level: int = logging.WARNING,'),
     ])],
     "RED", "不传 level 时必须是 ERROR"),

    ("M6 上报之后异常继续逃逸（非致命失败变成硬错误，兜底这件东西没了）",
     "tests/test_failure_alerting.py",
     [("repl", NONFATAL, [
         ('        exc_info=True,\n    )\n',
          '        exc_info=True,\n    )\n    raise exc\n'),
     ])],
     "RED", ("RuntimeError: warn-boom", "RuntimeError: boom")),

    # ── 告警邮件的门槛（core/alerting.py 的 ALERT_LEVEL）─────────────────────
    ("M7 告警门槛降到 WARNING：已经兜底的后台动作也开始发信",
     "tests/test_failure_alerting.py",
     [("repl", ALERTING, [('ALERT_LEVEL = logging.ERROR', 'ALERT_LEVEL = logging.WARNING')])],
     "RED", "WARNING 记成了告警"),

    ("M8 告警门槛升到 CRITICAL：线上 500 与「数据没存进去」都不再发信",
     "tests/test_failure_alerting.py",
     [("repl", ALERTING, [('ALERT_LEVEL = logging.ERROR', 'ALERT_LEVEL = logging.CRITICAL')])],
     "RED", ("必须发得出告警", "线上 500 却没发告警")),

    # ── 扫描面（§4 第 4 行：线上不再有「打印后吞掉」的失败）──────────────────
    ("M9 扫描面里出现一个解析不动的文件 → 判据当场炸在守卫上（不许静默跳过）",
     "tests/test_failure_alerting.py",
     [("write", PROBE, "def broken(:\n")],
     "RED", "扫描目标无法解析"),

    ("M10 一处吞错退回 `print`（失败只落容器 stdout，面板与告警都看不见）",
     "tests/test_failure_alerting.py",
     [("repl", CONTEXT, [
         ('            logger.warning("Character filter failed: %s", exc, exc_info=True)',
          '            print(f"[ContextEngine] Character filter failed: {exc}")'),
     ])],
     "RED", "这些地方的失败只落在容器 stdout 里"),

    ("M11 吞错写进 `else:` 分支（旧判据只读 `.body`，整类静默跳过）",
     "tests/test_failure_alerting.py",
     [("repl", DISTILLER, [
         ('        except RuntimeError:\n'
          '            merged = asyncio.run(_concurrent())\n'
          '        else:\n'
          '            merged = [self._single_reduce(b, character_name) for b in batches]\n',
          '        except RuntimeError:\n'
          '            merged = asyncio.run(_concurrent())\n'
          '        else:\n'
          '            print("[distill] probe failed")\n'
          '            merged = [self._single_reduce(b, character_name) for b in batches]\n'),
     ])],
     "RED", ("这些地方的失败只落在容器 stdout 里", "core/distiller.py")),

    ("M12 `print` 之后 `raise HTTPException` 不算交给日志（旧判据当「必然抛出」放过）",
     "tests/test_failure_alerting.py",
     [("repl", WEB_DISTILL, [
         ('        logger.warning("[distill] Card validation failed: %s", exc)\n',
          '        print(f"[distill] Card validation failed: {exc}")\n'),
     ])],
     "RED", ("这些地方的失败只落在容器 stdout 里", "web/routers/distill.py")),

    ("M13 唯一的豁免失效（那处被改好）→ 相等比较必须变红",
     "tests/test_failure_alerting.py",
     [("repl", ALERTING, [
         ('        print(f"发送失败: {type(exc).__name__}: {exc}", file=sys.stderr)\n',
          ''),
     ])],
     "RED", "改用 nonfatal 或模块 logger：[]"),

    # ── 第 4 节锁自身（宽 `except` 的相等比较）───────────────────────────────
    ("M14 一处宽 `except` 退回静默（连 stdout 都没有）→ 第 4 节的相等比较必须变红",
     "tests/test_failure_alerting.py",
     [("repl", CONTEXT, [
         ('            logger.warning("Character filter failed: %s", exc, exc_info=True)\n',
          '            pass\n'),
     ])],
     "RED", "宽 `except` 吞掉了失败却没有任何痕迹"),
]

GROUPS = {"M": MUTATIONS}


# ── 执行 ───────────────────────────────────────────────────────────────────


def _apply(edits):
    for kind, path, payload in edits:
        assert path.resolve() != TEST.resolve(), (
            "变异落在覆盖域的靶子文件上 —— 它自己的判别器行号会平移，红源坐标当场作废"
            "（本矩阵不该有这种变异）")
        if kind == "write":
            assert not path.exists(), f"{path.name} 已存在 —— `write` 只用于创建临时文件"
            path.write_text(payload, encoding="utf-8")
        elif kind == "repl":
            src = path.read_text(encoding="utf-8")
            for old, new in payload:
                hits = src.count(old)
                assert hits == 1, f"锚点在 {path.name} 命中 {hits} 次（应恰 1）：{old[:70]!r}"
                src = src.replace(old, new)
            path.write_text(src, encoding="utf-8")
        else:
            raise ValueError(f"未知动作 {kind}")


def _restore(baseline):
    for p, b in baseline.items():
        p.write_bytes(b)
    if PROBE.exists():                       # 创建出来的文件按名字删，不靠 _apply 的返回值
        PROBE.unlink()


def _run(target: str) -> tuple[str, list[str], set[str]]:
    """跑一次靶子，回（汇总行, 红源摘要, 红在仓内的 `文件:行号`）。

    拿不到汇总行时回 `lock_coverage.RUNAWAY` 并把退出码与尾部输出塞进红源 —— 跑不起来与
    全绿必须分开，否则「判据根本没执行」会一路读成「符合预期」。

    `--tb=long` 不是可选项：`--tb=line` 给的是最深帧，经由包装的分支会塌成包装里那一条
    （见 `lock_coverage` 的模块 docstring），覆盖闭合就无从谈起。
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "--tb=long"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout + r.stderr
    # 红源摘要要能看出**红在哪一句**，故除了 FAILED/ERROR 行与 AssertionError，还收 `--tb=long`
    # 里以 `E ` 开头的那些行 —— 非断言式失败（本矩阵 M6：非致命失败逃逸成 RuntimeError）在
    # FAILED 行里**不带异常原文**，只靠上面两条过滤就只剩一个 nodeid，标记无从匹配。
    keep = [ln.strip()[:300] for ln in out.splitlines()
            if ln.strip().startswith(("FAILED", "ERROR", "E ")) or "AssertionError" in ln]
    # **取最后一条**匹配行，不是第一条：失败用例的 traceback 里也可能出现「… errors in …」
    # （本矩阵 M1/M2/M3/M8 都是 `ExceptionGroup: unhandled errors in a TaskGroup`），
    # 取第一条会把那句当成汇总行 —— 汇总行是 pytest 最后打印的那句。
    summary = next((ln.strip() for ln in reversed(out.splitlines())
                    if ("passed" in ln or "failed" in ln or "error" in ln) and " in " in ln), None)
    if summary is None:
        keep.append(f"[退出码 {r.returncode}] 拿不到汇总行 —— 这条判据根本没跑起来")
        keep += [ln.strip()[:300] for ln in out.splitlines() if ln.strip()][-2:]
        summary = lock_coverage.RUNAWAY
    return summary, keep, lock_coverage.red_lines(out, ROOT)


def _baseline_gate() -> dict[str, str]:
    """先验基线：靶子绿（或本环境 skip）才开跑。

    两种不可用成因由 `lock_coverage.baseline_verdict` 分开（缺陷 45）——「跑不起来」（修
    环境）与「跑起来了但红」（修锁）的下一步动作不同。
    """
    summary, _, _ = _run("tests/test_failure_alerting.py")
    print(f"  基线 tests/test_failure_alerting.py  {summary}")
    cause = lock_coverage.baseline_verdict(summary)
    return {"tests/test_failure_alerting.py": cause} if cause else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列变异不跑（核对锚点与标记）")
    args = ap.parse_args()

    if args.list:
        for label, target, edits, expect, marker in MUTATIONS:
            paths = ", ".join(p.name for _k, p, _pl in edits)
            print(f"{label}\n    靶子={target}  期望={expect}  动={paths}  标记={marker!r}")
        return 0

    labels = [m[0] for m in MUTATIONS]
    assert len(set(labels)) == len(labels), "变异编号重复 —— 产物里会互相覆盖"

    baseline = {p: p.read_bytes() for p in TARGETS}
    assert not PROBE.exists(), f"{PROBE.name} 在开跑前就在树里 —— 基线不是干净的树"

    print("== 先验基线 ==")
    bad = _baseline_gate()
    if bad:
        return lock_coverage.refuse_on_baseline(bad)

    mismatches: list[str] = []
    hits: dict[str, list[str]] = {}
    skipped: list[str] = []
    for label, target, edits, expect, marker in MUTATIONS:
        _apply(edits)
        try:
            summary, keep, lines = _run(target)
        finally:
            _restore(baseline)
        got = lock_coverage.outcome(summary)
        want = {"RED": "RED"}.get(expect, "green")
        if want == "RED":
            # 只留落在覆盖域里的帧：stdlib / asyncio 内部 / 靶子之外的行号不算红源。
            hits[label] = sorted(l for l in lines if l.rsplit(":", 1)[0] in set(DOMAIN))
        if got == lock_coverage.RUNAWAY:
            mismatches.append(f"{label}：{lock_coverage.RUNAWAY} —— 判据没跑起来，"
                              "这条变异无法验证（既不是绿也不是红）")
        elif got != want:
            mismatches.append(f"{label}：期望 {want} 实得 {got}")
        marks = (marker,) if isinstance(marker, str) else marker
        missing = [m for m in marks if not any(m in k for k in keep)] if got != lock_coverage.RUNAWAY else []
        if missing:
            mismatches.append(f"{label}：红源里没有 {missing!r}（红的不是那条断言）")
        if want == "RED" and not hits.get(label):
            mismatches.append(f"{label}：红了但一条落入覆盖域的红源都没有（空转）")
        print(f"\n### {label}\n    期望={want}  实得={got}  红源={hits.get(label)}")
        for k in keep:
            print("   ", k)
        print("   >>", summary)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        got_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        same = got_hash == hashlib.sha256(baseline[p]).hexdigest()
        if not same:
            mismatches.append(f"{p.name} 还原后 sha256 不符")
        print(f"  {str(p.relative_to(ROOT)):34s} {same}  {got_hash[:16]}")
    if PROBE.exists():
        mismatches.append(f"{PROBE.name} 残留在树里（M9 创建的临时文件没删）")

    print("\n== 结论 ==")
    if mismatches:
        for m in mismatches:
            print("  MISMATCH", m)
        print("  矩阵有 mismatch —— 产物**不写**（写下去等于把没核对过的红源入库）。")
        return 1
    lock_coverage.write_artifact(ARTIFACT, "tests/perf/alerting_mutations.py",
                                 DOMAIN, hits, skipped, (), root=ROOT)
    print(f"  全部符合预期。产物已写：{ARTIFACT.relative_to(ROOT).as_posix()}")
    print("  （覆盖闭合由 tests/test_lock_coverage.py 核：判别器集合 == 被撞集合）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
