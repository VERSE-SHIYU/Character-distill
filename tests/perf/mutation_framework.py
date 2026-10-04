# -*- coding: utf-8 -*-
"""变异矩阵的**执行原语** —— 只放「怎么跑一次变异、怎么还原文」，不认任何领域。

原先这套 `_hidden` / `_TOUCHED` / `_apply` / `_restore` / `_run` / `_run_py` 抄在
`route_facts_mutations.py` 里，另有两个驱动 import 它、一个（alerting）又自抄了一份 ——
四份各自漂移（见 spec §3 的全量对照）。这里收成唯一一份；各驱动只留「变异表 + 门 +
预筛」，跑法统一交 `run_matrix`。

**不变量**（每条变异都成立，脚本自检，不是口头保证）
  1. **锚点恰一命中**：`repl` 的 `old` 必须恰命中一次，0/2 命中当场 assert（锚点漂移会
     静默变成「变异没生效」，那是最危险的假绿）。
  2. **逐字节还原**：施加与运行都在 `try` 里，`finally` 按基线字节还原；收尾核 sha256、
     隐藏文件残留、新建文件残留。
  3. **`write` 只用于新建**：写前断言路径不存在，路径记进 `_CREATED`，还原时删掉。
  4. **红源坐标可比**：`_run` 把红行号从「变异后」搬回「变异前」坐标系，搬不动就报出来。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

# 断言文案是中文，子进程与本进程都过一遍 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "tests"))
import lock_coverage  # noqa: E402  —— 帧解析、判档、产物写入的唯一一份实现


# ── 执行原语（自 route_facts_mutations.py 原样搬入，只做 spec §3 列的统一改动）──────


def _hidden(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.name + ".hidden")


# 本次变异动过的 .py：仓内相对 posix 路径 → **变异前**的源文本。`_run` 用它把红行号搬回
# 变异前的坐标系（见 `lock_coverage.realign_hits`）。`hide` 不记 —— 移走的文件运行时一条
# 判别器都不剩，红不可能落在里面。`write`（新建）也不记：它没有「变异前」的文本。
_TOUCHED: dict[str, str] = {}

# `write` 动作新建出来的文件（绝对路径）。它们不在基线里，`_restore` 按这份清单删。
_CREATED: set[pathlib.Path] = set()


def _apply(edits):
    for kind, path, payload in edits:
        if kind != "hide" and path.suffix == ".py" and path.exists():
            _TOUCHED.setdefault(path.resolve().relative_to(ROOT).as_posix(),
                                path.read_text(encoding="utf-8"))
        if kind == "append":
            path.write_text(path.read_text(encoding="utf-8") + payload, encoding="utf-8")
        elif kind == "write":
            assert not path.exists(), f"{path.name} 已存在 —— `write` 只用于创建临时文件"
            path.write_text(payload, encoding="utf-8")
            _CREATED.add(path.resolve())
        elif kind == "hide":
            path.rename(_hidden(path))
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
        hid = _hidden(p)
        if hid.exists():
            hid.unlink()
        p.write_bytes(b)
    for p in _CREATED:
        if p.exists():
            p.unlink()
    _CREATED.clear()
    _TOUCHED.clear()


def summary_of(out: str) -> str | None:
    """pytest 输出里的**汇总行**：取**最后一条**含 passed/failed/error 且含 ` in ` 的行。

    **取最后一条，不是第一条。** 失败用例的 traceback 里也可能出现「… error … in …」
    （如 `assert any(... "error" in p for ...)`），取第一条会把那句源码当成汇总行 ——
    汇总行是 pytest 最后打印的那句。
    """
    return next((ln.strip() for ln in reversed(out.splitlines())
                 if ("passed" in ln or "failed" in ln or "error" in ln) and " in " in ln), None)


def _run(target: str) -> tuple[str, list[str], set[str], list[str]]:
    """跑一条靶子，回（结论行, 红源摘要, 红在**变异前**坐标系的哪一行, 搬移不动的说明）。

    第一个返回值是汇总行，**不是判档** —— 判档由 `lock_coverage.outcome` 一处做。拿不到
    汇总行时回 `lock_coverage.RUNAWAY` 并把退出码与尾部输出塞进红源摘要：跑不起来与全绿
    必须分开，否则「判据根本没执行」会一路读成「符合预期」。

    第三个返回值必须用 `--tb=long`：`--tb=line` 给的是**最深帧**，经由抛错包装的分支会全部
    塌成包装里那条 `raise`，覆盖闭合就无从谈起。`--tb=long` 的帧头是 `路径:行号:`，由
    `lock_coverage.red_lines` 解析。解析漂移时是响的：解析得空集 → 覆盖闭合当场红。

    解析出来的行号是**变异后**那次运行的行号，而判别器集合算在**变异前**的文件上；变异
    只要往靶子文件里插/删一行，插点之后的判别器行号就整体平移 —— 故这里搬回变异前的
    坐标系再交出去，搬不动就报出来。
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "--tb=long"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout + r.stderr
    # 红源摘要要能看出**红在哪一句**：除了 FAILED/ERROR 行与 AssertionError，还收 `--tb=long`
    # 里以 `E ` 开头的行 —— 非断言式失败在 FAILED 行里**不带异常原文**，只靠上面两条过滤就
    # 只剩一个 nodeid，标记无从匹配。
    keep = [ln.strip()[:300] for ln in out.splitlines()
            if ln.strip().startswith(("FAILED", "ERROR", "E ")) or "AssertionError" in ln]
    summary = summary_of(out)
    if summary is None:
        keep.append(f"[退出码 {r.returncode}] 拿不到汇总行 —— 这条判据根本没跑起来")
        keep += [ln.strip()[:300] for ln in out.splitlines() if ln.strip()][-2:]
        summary = lock_coverage.RUNAWAY
    raw = lock_coverage.red_lines(out, ROOT)
    mutated = {rel: (ROOT / rel).read_text(encoding="utf-8")
               for rel in _TOUCHED if (ROOT / rel).exists()}
    lines, problems = lock_coverage.realign_hits(_TOUCHED, mutated, raw)
    if lines != raw:
        print(f"   [坐标] 变异后的红行号 → 变异前的行号：{sorted(raw)} → {sorted(lines)}")
    return summary, keep, lines, problems


def _run_py(code: str) -> tuple[str, list[str]]:
    """跑一段一次性 Python（route_facts 的 `_ORDER_SWAP` 用它，非 pytest 靶子）。"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout + r.stderr
    bad = [ln.strip()[:300] for ln in out.splitlines() if "Error" in ln or "assert" in ln]
    return ("OK" if r.returncode == 0 else f"退出码 {r.returncode}"), bad


# ── 主循环（各驱动共用）───────────────────────────────────────────────────────


def run_matrix(items, *, domain, targets, artifact, driver_rel, root=ROOT,
               may_skip=frozenset(), pre_skipped=()) -> int:
    """跑完整张变异表：逐条施加 → 跑 → 还原 → 判档归类；无 mismatch 才写产物。

    `items`：`(label, target, edits, expect[, marker])`。
      - `target` 是字符串 → `_run(target)`，判档由 `lock_coverage.outcome` 做。
      - `target` 是**可调用对象** → 直接调用，返回 `(got, keep, lines, problems)`，`got`
        已是判档（route_facts 的 `_ORDER_SWAP` 用这个口子）。这是唯一新开的口子。
    `domain`：覆盖域（仓内相对 posix 路径）。由驱动传入（按**全部**组算，与 `--group` 无关）。
    `may_skip`：允许 skip 的编号集合 —— 登记过的进产物 `skipped`，没登记的记 mismatch。
    `pre_skipped`：驱动预筛掉的编号（route_facts 的 `RED-container`），原样进产物 `skipped`。

    返回退出码：0 = 全部符合预期（产物已写），1 = 有 mismatch（产物**不写**）。
    """
    labels = [item[0] for item in items]
    assert len(set(labels)) == len(labels), "变异编号重复 —— 产物里会互相覆盖"

    baseline = {p: p.read_bytes() for p in targets}
    mismatches: list[str] = []
    hits: dict[str, list[str]] = {}
    controls: list[str] = []
    skipped: list[str] = list(pre_skipped)
    dom = set(domain)
    # 本轮所有 `write` 新建过的路径：`_restore` 每条都删，收尾再核一遍 —— 删没删掉要点名，
    # 不能只信 `_restore` 自己（残留的新建文件会被下一条变异、下一个驱动当成树的一部分）。
    created_all: set[pathlib.Path] = set()

    for item in items:
        label, target, edits, expect = item[:4]
        marker = item[4] if len(item) > 4 else None
        try:
            # `_apply` 也在 try 里：一条变异有多处改动时，后一处锚点没命中会当场 assert，
            # 前一处已经写进树了 —— 不还原就带着半个变异退出。
            _apply(edits)
            if callable(target):
                got, keep, lines, problems = target()
            else:
                summary, keep, lines, problems = _run(target)
                got = lock_coverage.outcome(summary)
        finally:
            created_all |= _CREATED
            _restore(baseline)
        if problems:
            mismatches.append(f"{label}：{'；'.join(problems)}")
        # **期望红的进 `hits`，期望绿/OK（红源天生为空）的进 `controls`。** 一条「期望绿」的
        # 变异（反证）红源为空正是它要证的事；混进 hits 会被元锁记成「空转」—— 反证与空转
        # 共用一个信号，正是本模块反复防的那副面孔。
        want = {"RED": "RED", "RED-container": "RED", "OK": "OK"}.get(expect, "green")
        if want == "RED":
            # 只留落在覆盖域里的帧：stdlib / asyncio 内部 / 靶子之外的行号不算红源。
            hits[label] = sorted(l for l in lines if l.rsplit(":", 1)[0] in dom)
        else:
            controls.append(label)
        if got == lock_coverage.RUNAWAY:
            # 不记绿也不记红：这一跑里判据根本没执行，红源与覆盖都无从谈起。
            mismatches.append(
                f"{label}：{lock_coverage.RUNAWAY} —— 判据没跑起来，这条变异无法验证"
                "（既不是绿也不是红；修好 import/runtime 再跑）")
        elif got == lock_coverage.SKIP:
            if label not in may_skip:
                mismatches.append(f"{label}：判据被 skip 了（该编号未登记为「本环境不适用」）")
            # 走了 skip 的条目从覆盖域里摘出去：它一条判别器都没撞到，留着会被元锁记成
            # 「空转」—— 而它红/绿/空转在这台机器上根本无从谈起。
            hits.pop(label, None)
            if label in controls:
                controls.remove(label)
            skipped.append(label)
            print(f"\n### {label}   期望={want}  实得={got}（不计红绿）")
            if not callable(target):
                print("   >>", summary)
            continue
        elif got != want:
            mismatches.append(f"{label}：期望 {want} 实得 {got}")
        # marker 可为单个串或一串（一条变异可能同时该红两条断言）—— 全部命中才算符合，
        # 缺一即记 mismatch。跑不起来时红源本来就不存在，跳过。
        marks = (marker,) if isinstance(marker, str) else (marker or ())
        missing = [m for m in marks if not any(m in k for k in keep)] \
            if got != lock_coverage.RUNAWAY else []
        if missing:
            mismatches.append(f"{label}：红源里没有 {missing!r}（红的不是那条断言）")
        if want == "RED" and not hits.get(label):
            mismatches.append(f"{label}：红了但一条落入覆盖域的红源都没有（空转）")
        print(f"\n### {label}   期望={want}  实得={got}  红源={hits.get(label)}")
        for k in keep:
            print("   ", k)
        if not callable(target):
            print("   >>", summary)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in targets:
        got_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        same = got_hash == hashlib.sha256(baseline[p]).hexdigest()
        if not same:
            mismatches.append(f"{p.name} 还原后 sha256 不符")
        if _hidden(p).exists():
            mismatches.append(f"{_hidden(p).name} 残留在树里（移走的文件没还原）")
        print(f"  {str(p.relative_to(root)):38s} {same}  {got_hash[:16]}")
    for p in sorted(created_all):
        if p.exists():
            mismatches.append(f"{p.name} 残留在树里（变异新建的文件没清掉）")
        print(f"  {p.name:38s} {'不存在' if not p.exists() else '仍在！'}")

    print("\n== 结论 ==")
    if mismatches:
        for m in mismatches:
            print("  MISMATCH", m)
        print("  矩阵有 mismatch —— 产物**不写**（写下去等于把没核对过的红源入库）。")
        return 1
    lock_coverage.write_artifact(artifact, driver_rel, domain, hits, skipped, controls, root=root)
    print(f"  全部符合预期。产物已写：{pathlib.Path(artifact).relative_to(root).as_posix()}")
    print("  （覆盖闭合由 tests/test_lock_coverage.py 核：判别器集合 == 被撞集合）")
    return 0
