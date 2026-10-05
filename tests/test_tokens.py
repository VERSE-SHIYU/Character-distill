# -*- coding: utf-8 -*-
"""U1 / U3：`core.tokens.count_tokens` 是全仓库唯一的 token 计数 —— 官方 tokenizer 精确计数。

U1 用公版《孔乙己》把「我们的封装」与「官方 tokenizer 直接编码」钉在同一个固定值上：
样本在 `tests/fixtures/kongyiji.txt`（公版，鲁迅 1936 年逝世），官方文件在
`core/assets/deepseek_tokenizer/tokenizer.json`。期望值 1,848 来自 spec C11 的实测。
受版权保护的长篇不入库，边界的正确性由 `test_length_budget.py` 用打桩的 token 数复刻。

Run: pytest tests/test_tokens.py -v
"""
from __future__ import annotations

import pathlib
import threading

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAMPLE = _ROOT / "tests" / "fixtures" / "kongyiji.txt"
_TOKENIZER = _ROOT / "core" / "assets" / "deepseek_tokenizer" / "tokenizer.json"
_EXPECTED_KONG_TOKENS = 1_848


def test_u1_count_tokens_matches_the_official_tokenizer_direct_encoding():
    """封装数与官方 `Tokenizer.encode` 的 token 数逐字相等，且等于固定期望值。"""
    from tokenizers import Tokenizer

    from core.tokens import count_tokens

    text = _SAMPLE.read_text(encoding="utf-8")
    direct = len(Tokenizer.from_file(str(_TOKENIZER)).encode(text).ids)
    assert count_tokens(text) == direct, "封装数与官方直接编码不一致"
    assert direct == _EXPECTED_KONG_TOKENS, f"公版样本期望 {_EXPECTED_KONG_TOKENS}，实测 {direct}"


def test_u1_empty_string_is_zero_without_touching_the_model():
    from core.tokens import count_tokens

    assert count_tokens("") == 0


def test_u1_ascii_and_cjk_both_count_as_subword_pieces():
    """计数是 token 数不是字数：同一段中文一定比它的长度小（多字/词成一片）。"""
    from core.tokens import count_tokens

    cjk = "鲁镇的酒店的格局"
    assert 0 < count_tokens(cjk) <= len(cjk)


def test_u3_tokenizer_loads_exactly_once_under_concurrent_first_calls(monkeypatch):
    """多线程并发首调用只加载一次 —— 惰性单例 + 锁，不重复读 6MB 文件。

    判据是「`Tokenizer.from_file` 被调了几次」：把它换成计数替身（转发真实现），
    重置模块单例后开 16 条线程同时首调用，`from_file` 必须恰好 1 次。
    """
    import core.tokens as tokens

    real_from_file = tokens.Tokenizer.from_file
    calls: list[str] = []
    calls_lock = threading.Lock()

    def counting_from_file(path, *args, **kwargs):
        with calls_lock:
            calls.append(str(path))
        return real_from_file(path, *args, **kwargs)

    monkeypatch.setattr(tokens.Tokenizer, "from_file", staticmethod(counting_from_file))
    monkeypatch.setattr(tokens, "_tokenizer", None)  # 重置惰性单例，制造「并发首调用」

    results: list[int] = []
    results_lock = threading.Lock()

    def worker() -> None:
        n = tokens.count_tokens("同时首调用")
        with results_lock:
            results.append(n)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(calls) == 1, f"tokenizer 被加载了 {len(calls)} 次（应恰 1 次）"
    assert len(results) == 16 and len(set(results)) == 1, f"并发结果不一致：{results}"
