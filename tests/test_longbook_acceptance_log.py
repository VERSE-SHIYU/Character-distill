# -*- coding: utf-8 -*-
"""`tests/perf/longbook_acceptance.py` 的 D1/D2 读数锁：日志计数与停下条件。

**为什么入库。** §10 D2 的判据全落在「日志里数出来几个」上，而这个数原先只有跑真验收时
才看得见。两种坏法都不报错、只让 D3 那行读数安静地骗人：计数正则写松（把 `Map chunk 9
done` 也数进去），或**停下条件与计数标签脱钩**（标签改了名、`over_limit` 还认旧名，于是
「任一分片最终失败」永远不停）。

**样例日志而不是真日志。** `count_log` / `over_limit` 是纯函数（不碰网络、不碰库），所以
每条该数的行都配一条「像但它不是」的反例 —— 计数多一个少一个、或停下条件挪了位置，都会红。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "perf"))

import longbook_acceptance as acc  # noqa: E402

SAMPLE_LOG = """\
2026-09-26 00:00:01,000 INFO distilled 0/242
2026-09-26 00:00:02,100 WARNING Map chunk 7 failed: APITimeoutError
2026-09-26 00:00:03,200 WARNING Map chunk 241 failed: APIConnectionError
2026-09-26 00:00:04,300 WARNING Map chunk 9 done in 1.2s
2026-09-26 00:00:05,400 WARNING Map chunk 3 slow: retrying
2026-09-26 00:00:06,500 WARNING Reduce batch 失败：截断（finish_reason=length）
INFO:     127.0.0.1:51000 - "POST /api/distill/start HTTP/1.1" 429 Too Many Requests
INFO:     127.0.0.1:51001 - "POST /api/distill/task/abc HTTP/1.1" 503 Service Unavailable
读取流式响应失败（上游传输层）：ReadTimeout
"""

EXPECTED_COUNTS = {
    "429": 1, "5xx": 1, "读取流式响应失败": 1, "Reduce batch": 1, "分片失败": 2,
}


def _log(tmp_path, text: str) -> str:
    p = tmp_path / "app.log"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _clean_env() -> dict:
    """A 的对齐门逐项相等 —— 让 `over_limit` 只可能因日志计数而停下。"""
    return {
        "a1": [(k, v, v) for k, v in (
            ("chunk_size", acc.EXPECTED_CHUNK_SIZE),
            ("map_concurrency", acc.EXPECTED_MAP_CONCURRENCY),
            ("model", acc.EXPECTED_MODEL),
            ("thinking", "关"),
        )],
        "chars": acc.EXPECTED_CHARS,
        "identify_chunks": acc.EXPECTED_IDENTIFY_CHUNKS,
    }


def _stats(elapsed_s: float = 10.0) -> dict:
    return {"mode": "distill", "elapsed_s": elapsed_s, "stages": {}}


def test_sample_log_counts_the_real_lines_and_not_the_lookalikes(tmp_path):
    assert acc.count_log(_log(tmp_path, SAMPLE_LOG)) == EXPECTED_COUNTS


def test_missing_log_is_reported_as_unmeasured_not_as_zero():
    """没给日志 ≠ 数出来是 0：新计数同样不许把「未提供」当命中。"""
    counts = acc.count_log(None)
    assert set(counts.values()) == {"未提供"}
    assert acc.over_limit(_stats(), counts, _clean_env()) == []


def test_a_single_failed_chunk_stops_the_run(tmp_path):
    """D2「任一分片最终失败」要真的挂在计数标签上，不是挂在一个已经没人写的名字上。"""
    counts = acc.count_log(_log(tmp_path, "WARNING Map chunk 7 failed: Boom\n"))
    why = acc.over_limit(_stats(), counts, _clean_env())
    assert len(why) == 1 and why[0].startswith("分片失败")


def test_429_is_counted_but_no_longer_stops(tmp_path):
    """WP14：429 是闸收敛的正常信号，只报次数。"""
    text = "".join('INFO: 1.2.3.4 - "POST /api/distill/start HTTP/1.1" 429 Too Many Requests\n'
                   for _ in range(3))
    counts = acc.count_log(_log(tmp_path, text))
    assert counts["429"] == 3
    assert acc.over_limit(_stats(), counts, _clean_env()) == []


def test_5xx_still_stops(tmp_path):
    counts = acc.count_log(_log(tmp_path, 'INFO: 1.2.3.4 - "GET /x HTTP/1.1" 502 Bad Gateway\n'))
    why = acc.over_limit(_stats(), counts, _clean_env())
    assert len(why) == 1 and why[0].startswith("5xx")
