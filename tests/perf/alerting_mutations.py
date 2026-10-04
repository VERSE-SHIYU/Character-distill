# -*- coding: utf-8 -*-
"""spec-119 变异矩阵驱动 —— 14 条变异打 `tests/test_failure_alerting.py` 的 16 条判别器。

**为什么入库。** 本份交付报告里的「哪条变异红、红在哪句」全部来自这个脚本。数字若只骑在
仓外脚本上，三个月后复现不了（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**本份守的是什么。** 失败可见这条链上有两处落点：往上抛的错误由 `web/server.py` 的全局
处理器统一记录，被吞掉的错误由各落点自己记录。两处的判据都必须**装真的告警出口**才判得
准 —— 告警邮件只收 ERROR（WARNING 只落 stdout），于是「记没记」与「记成哪一档」必须
分开断言。矩阵按这两处各自的分支逐条打。

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
import pathlib
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
sys.path.insert(0, str(ROOT / "tests" / "perf"))
import lock_coverage  # noqa: E402  —— 帧解析与产物写入的唯一一份实现
import mutation_framework as framework  # noqa: E402  —— 执行原语的唯一一份实现

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
    # 锚点都在**全仓唯一**的位置上：`_report` 的 log 调用、默认档常量、`_report` 的
    # `exc_info=` 行。判据 2（锚点恰一命中）不是走过场：`except Exception as exc:` 那段
    # 在同步／异步两版里逐字相同，锚在它上面必然命中两处 —— 从前靠「只存在一版」侥幸
    # 过关，抽出 `_report`、补上同步版之后就不成立了，于是三条锚点全部改到唯一处。
    # 默认档同理：原先两版签名各写一遍 `= logging.ERROR`，提成 `DEFAULT_LEVEL` 后
    # 才既唯一、又真的覆盖「两版共用同一档」这件事。
    ("M4 nonfatal 忽略调用方给的 level，一律按 ERROR 记（兜底动作开始发邮件）",
     "tests/test_failure_alerting.py",
     [("repl", NONFATAL, [('    _logger.log(\n        level,',
                           '    _logger.log(\n        logging.ERROR,')])],
     "RED", "应恰有一条 WARNING"),

    ("M5 nonfatal 的默认档降到 WARNING（数据没存进去 / 请求失败不再发邮件）",
     "tests/test_failure_alerting.py",
     [("repl", NONFATAL, [
         ('DEFAULT_LEVEL = logging.ERROR', 'DEFAULT_LEVEL = logging.WARNING'),
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
# 执行原语（`_apply` / `_restore` / `_run`）已移到 `mutation_framework.py`，本文件不再自抄
# 一份（spec §3 收口）。本文件原有一条「变异不许落在覆盖域的靶子文件上」的门（`_apply` 里
# 的 assert），现改成开跑前的门：见 `_domain_edits`。


def _domain_edits(items) -> list[str]:
    """edits 里路径落在**覆盖域靶子文件**（`TEST`）上的变异编号。

    这类变异会让靶子文件的判别器行号整体平移，红源坐标当场作废 —— 在本矩阵里不该出现，
    出现即拒跑（原先是 `_apply` 里的一条 assert，改成开跑前遍历整表，见 `main`）。
    """
    out: list[str] = []
    for item in items:
        for _kind, path, _payload in item[2]:
            if path.resolve() == TEST.resolve():
                out.append(item[0])
    return out


def _baseline_gate() -> dict[str, str]:
    """先验基线：靶子绿（或本环境 skip）才开跑。

    两种不可用成因由 `lock_coverage.baseline_verdict` 分开（缺陷 45）——「跑不起来」（修
    环境）与「跑起来了但红」（修锁）的下一步动作不同。
    """
    summary, _, _, _ = framework._run("tests/test_failure_alerting.py")
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

    # 门：变异不许落在覆盖域的靶子文件上 —— 行号会平移，红源坐标当场作废。
    offenders = _domain_edits(MUTATIONS)
    if offenders:
        print("\n变异落在覆盖域的靶子文件上，拒绝跑（红源坐标会作废）：\n  "
              + "\n  ".join(offenders))
        return 2
    assert not PROBE.exists(), f"{PROBE.name} 在开跑前就在树里 —— 基线不是干净的树"

    print("== 先验基线 ==")
    bad = _baseline_gate()
    if bad:
        return lock_coverage.refuse_on_baseline(bad)

    items = [(label, target, edits, expect, marker)
             for (label, target, edits, expect, marker) in MUTATIONS]
    return framework.run_matrix(
        items, domain=DOMAIN, targets=TARGETS, artifact=ARTIFACT,
        driver_rel="tests/perf/alerting_mutations.py")


if __name__ == "__main__":
    raise SystemExit(main())
