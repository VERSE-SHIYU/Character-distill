"""P14 evidence: ChatEngine._window_start 值不变（旧位置配对 vs 新角色配对）。

文档 `docs/specs/history-digest.md` §11 引用本脚本的数字。运行：

    python docs/specs/artifacts/p14_window_start_equivalence.py

对「末尾为 assistant、有无角色开场白」的成对历史，穷举一串预算，逐位比对两种实现的返回值。
旧实现 = 位置配对（i 从末尾每步 -2，假定 user 开头）；新实现 = `history_digest.turn_spans`。
两者在成对历史上应逐位相同 —— 本脚本打印直方图与不一致处，最后一行 PASS/FAIL 供引用。
产物重定向到同目录 `p14_window_start_equivalence.out.txt`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import history_digest as hd  # noqa: E402
from core.tokens import count_tokens  # noqa: E402


def old_window_start(history: list[dict], budget: int) -> int:
    used = 0
    i = len(history) - 1
    while i >= 1:
        pair_tok = (count_tokens(history[i - 1].get("content", ""))
                    + count_tokens(history[i].get("content", "")))
        if used + pair_tok > budget:
            break
        used += pair_tok
        i -= 2
    return i + 1


def new_window_start(history: list[dict], budget: int) -> int:
    used = 0
    start = len(history)
    for u, a in reversed(hd.turn_spans(history)):
        pair_tok = (count_tokens(history[u].get("content", ""))
                    + count_tokens(history[a].get("content", "")))
        if used + pair_tok > budget:
            break
        used += pair_tok
        start = u
    return start


def _history(n_turns: int, opening: bool) -> list[dict]:
    msgs: list[dict] = []
    if opening:
        msgs.append({"role": "assistant", "content": "开场白"})
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"u{i} " + "字" * (i % 7)})
        msgs.append({"role": "assistant", "content": f"a{i} " + "话" * (i % 5)})
    return msgs


def main() -> None:
    histories = [(n, op) for n in (0, 1, 2, 6, 7, 13) for op in (False, True)]
    budgets = list(range(0, 400, 7))
    mismatches: list[tuple] = []
    for n, op in histories:
        h = _history(n, op)
        for b in budgets:
            o, w = old_window_start(h, b), new_window_start(h, b)
            if o != w:
                mismatches.append((n, op, b, o, w))
    print(f"histories={len(histories)} budgets={len(budgets)} "
          f"comparisons={len(histories) * len(budgets)} mismatches={len(mismatches)}")
    for m in mismatches[:20]:
        print("  mismatch", m)
    print("PASS" if not mismatches else "FAIL")
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
