# -*- coding: utf-8 -*-
"""distill-capacity 变异矩阵驱动 —— §5 的 M1–M13 各有专属红源（`docs/specs/distill-capacity.md`）。

覆盖域是新锁 `tests/test_length_budget.py`（`test_length_budget.py` 的模块 docstring 已写明
「本文件是变异驱动的覆盖域」）。跑法交 `mutation_framework.run_matrix` —— 与另外几个驱动共用
一份执行原语；本文件只留变异表与基线门。

**M9 不在此表。** §5 的 M9（前端接口失败时放行 → F1）落在 `web/frontend` 的 vitest 文件上，
而本框架的覆盖域、判别器解析（Python AST）、红源解析（pytest `--tb=long`）都只认 `.py` ——
前端变异无从入域。F1 的覆盖走 §6 的 `cd web/frontend; npm test`，故本驱动收 M1–M8、M10–M17。

**M14–M17 是 §5 之外的补位**（U5/R7/R9/R3-chat 四条判据的专属红源）：§5 只列到 M13，但那几条
判别器没有被任何 M1–M13 撞到，`tests/test_lock_coverage.py` 会当场判「名单外缺口」。它们各自
只改一处、且改法就是「修复前代码长什么样」（写死字面量 / 篡改返回值 / 不判 / 回字数估算）。

用法：python tests/perf/distill_capacity_mutations.py        （需测试 PG：docker-compose.test.yml）
"""
from __future__ import annotations

import pathlib
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

import lock_coverage  # noqa: E402  —— 判档与产物写入的唯一一份实现
import mutation_framework as framework  # noqa: E402  —— 执行原语的唯一一份实现

ARTIFACT = PERF_DIR / "distill_capacity_red_lines.json"

TARGET = "tests/test_length_budget.py"
LEN = ROOT / "core" / "length_budget.py"
TM = ROOT / "core" / "text_manager.py"
DIST = ROOT / "core" / "distiller.py"
ADAPT = ROOT / "adapters" / "llm_adapter.py"
ROUTE = ROOT / "web" / "routers" / "text.py"
CHAT = ROOT / "core" / "chat_engine.py"
UTILS = ROOT / "core" / "utils.py"
GROUP = ROOT / "core" / "group_session.py"

TARGETS = (LEN, TM, DIST, ADAPT, ROUTE, CHAT, UTILS, GROUP)

MUTATIONS = [
    # 撞到 U2 / R4 / R5：阈值本身也算一次读完
    ('M1  fits_one_pass 用 <=（恰等于阈值走一次读完，不再是分片）',
     TARGET, [("repl", LEN, [
         ('    return n_tokens < threshold',
          '    return n_tokens <= threshold'),
     ])], "RED"),
    # 撞到 R1：文件上传不再判长度
    ('M2  文件上传绕过 _check_length（不再判长度）',
     TARGET, [("repl", TM, [
         ('        if not parsed or not parsed.strip():\n'
          '            raise ValueError(_MSG["empty_after_parse"])\n\n'
          '        original_chars = len(parsed)\n'
          '        await self._check_length(parsed, text_type)\n',
          '        if not parsed or not parsed.strip():\n'
          '            raise ValueError(_MSG["empty_after_parse"])\n\n'
          '        original_chars = len(parsed)\n'),
     ])], "RED"),
    # 撞到 R1 / R3：小说按字数判，不按 token
    ('M3  check_upload 对小说按字数判（len 换掉 count_tokens）',
     TARGET, [("repl", LEN, [
         ('    n = count_tokens(text)\n    if not fits_one_pass(n):',
          '    n = len(text)\n    if not fits_one_pass(n):'),
     ])], "RED"),
    # 撞到 R1 线程断言：计数回到事件循环线程
    ('M4  长度计数不放线程（to_thread 去掉，直接同步调用）',
     TARGET, [("repl", TM, [
         ('        await asyncio.to_thread(check_upload, parsed, text_type)',
          '        check_upload(parsed, text_type)'),
     ])], "RED"),
    # 撞到 R4 / R5 / S2：选路径换回字数估算
    ('M5  选路径换回字数估算（count_tokens → len × 0.6）',
     TARGET, [("repl", DIST, [
         ('        return fits_one_pass(count_tokens(text), self._longctx_threshold)',
          '        return fits_one_pass(int(len(text) * 0.6), self._longctx_threshold)'),
     ])], "RED"),
    # 撞到 R6：超窗不再上「文本过长」
    ('M6  _upstream_user_message 不判超窗（去掉超窗分支）',
     TARGET, [("repl", ADAPT, [
         ('    if _is_context_overflow(exc):\n        return "文本过长，超出模型一次能处理的长度，请缩短后再试"\n',
          ''),
     ])], "RED"),
    # 撞到 R6 反例：超窗判定不看状态码
    ('M7  _is_context_overflow 不看状态码（只看措辞）',
     TARGET, [("repl", ADAPT, [
         ('    if _status_code(exc) != 400:\n        return False\n    text = str(exc)\n'
          '    return any(wording in text for wording in _CONTEXT_OVERFLOW_WORDINGS)',
          '    text = str(exc)\n'
          '    return any(wording in text for wording in _CONTEXT_OVERFLOW_WORDINGS)'),
     ])], "RED"),
    # 撞到 R2：413 文案写死，不再由常量生成
    ('M8  413 文案写死（脱离 MAX_FILE_BYTES 常量）',
     TARGET, [("repl", ROUTE, [
         ('    return f"文件体积超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 上限，请压缩后重试"',
          '    return "文件体积超过 100MB 上限，请压缩后重试"'),
     ])], "RED"),
    # 撞到 U4：导入时预算断言的可复用判据被删
    ('M10 assert_budget_fits 的断言删掉（越窗也放行）',
     TARGET, [("repl", LEN, [
         ('    assert threshold + reserve + output_max < window, (\n'
          '        f"预算越窗：{threshold} + {reserve} + {output_max} >= {window} "\n'
          '        "（阈值、提示词预留与输出上限之和超过了模型上下文窗口）"\n'
          '    )',
          '    return'),
     ])], "RED"),
    # 撞到 R8 / S2：聊天预算改回 len × 0.8
    ('M11 聊天上下文预算改回 len × 0.8（不再按精确计数）',
     TARGET, [("repl", CHAT, [
         ('            pair_tok = (\n'
          '                count_tokens(user_msg.get("content", ""))\n'
          '                + count_tokens(asst_msg.get("content", ""))\n'
          '            )',
          '            pair_tok = int(len(user_msg.get("content", "")) * 0.8)'),
     ])], "RED"),
    # 撞到 R10 / S2：用量兜底改回按字数除 1.5
    ('M12 用量兜底改回按字数除 1.5（不再按精确计数）',
     TARGET, [("repl", UTILS, [
         ('        "prompt_tokens": count_tokens(prompt_text),\n'
          '        "completion_tokens": count_tokens(completion_text),',
          '        "prompt_tokens": int(len(prompt_text) / 1.5),\n'
          '        "completion_tokens": int(len(completion_text) / 1.5),'),
     ])], "RED"),
    # 撞到 R10（流式）：兜底只计最后一片
    ('M13 流式兜底只计最后一片（不累加每一片）',
     TARGET, [("repl", DIST, [
         ('                    parts.append(next(stream))',
          '                    parts = [next(stream)]'),
     ])], "RED"),
    # 撞到 U5：public_limits 的字段各写一份字面量，脱离常量
    ('M14 public_limits 的 story 上限写死为 1_000_000（脱离 LONGCTX_THRESHOLD_TOKENS）',
     TARGET, [("repl", LEN, [
         ('        "story_max_tokens": LONGCTX_THRESHOLD_TOKENS,',
          '        "story_max_tokens": 1_000_000,'),
     ])], "RED"),
    # 撞到 R7：limits 接口不再原样回常量
    ('M15 limits 接口篡改一个字段（不再原样回 public_limits）',
     TARGET, [("repl", ROUTE, [
         ('    return public_limits()',
          '    return {**public_limits(), "chat_max_chars": 1}'),
     ])], "RED"),
    # 撞到 R9：群聊预算改回字数估算
    ('M16 群聊历史预算改回按字数（count_tokens → len）',
     TARGET, [("repl", GROUP, [
         ('            total = sum(count_tokens(m["content"]) for m in messages)',
          '            total = sum(len(m["content"]) for m in messages)'),
     ])], "RED"),
    # 撞到 R3（聊天分支）：聊天不再按字数判上限
    ('M17 check_upload 聊天分支不判上限（if n > CHAT_MAX_CHARS → if False）',
     TARGET, [("repl", LEN, [
         ('        if n > CHAT_MAX_CHARS:',
          '        if False:'),
     ])], "RED"),
]

GROUPS = {"M": MUTATIONS}


def _baseline_gate() -> dict[str, str]:
    """先验基线：覆盖域的靶子锁绿才开跑 —— 否则「变异后红」说不清红源。"""
    summary, _, _, _ = framework._run(TARGET)
    print(f"  基线 {TARGET}  {summary}")
    cause = lock_coverage.baseline_verdict(summary)
    return {TARGET: cause} if cause else {}


def main() -> int:
    print("== 先验基线 ==")
    bad = _baseline_gate()
    if bad:
        return lock_coverage.refuse_on_baseline(bad)
    return framework.run_matrix(
        MUTATIONS, domain=lock_coverage.domain_of(GROUPS), targets=TARGETS, artifact=ARTIFACT,
        driver_rel="tests/perf/distill_capacity_mutations.py")


if __name__ == "__main__":
    sys.exit(main())
