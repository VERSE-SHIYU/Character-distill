"""重放脚本 `scripts/replay_session_record.py` 的判定线离线检查（不调真模型）。

判定线决定能不能开 PR；它自己算错了就会放过坏结果。这里用造出来的日志各打一遍：
干净的一份 6 条全过；把上一场验收的几种毛病逐个塞进去，对应那条必须不过。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from core import session_record as rec

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "replay_session_record", ROOT / "scripts" / "replay_session_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _e(id_, col, text, evidence, replaces=None, source="user"):
    return {"id": id_, "col": col, "text": text, "evidence": evidence, "source": source,
            "active": True, "replaces": replaces}


def _clean_log():
    return [
        {"turn": 1, "kind": "seed", "new": [_e(1, "known", "对方叫林小满", "我叫林小满")]},
        {"turn": 2, "kind": "seed", "new": [_e(2, "plans", "对方下月去京城", "我下个月去京城")]},
        {"turn": 3, "kind": "fill", "new": []},
        {"turn": 4, "kind": "change", "new": [_e(3, "plans", "对方改去金陵", "改去金陵", replaces=2)]},
        {"turn": 5, "kind": "question", "new": []},
    ]


def _record(log):
    entries = {}
    for row in log:
        for e in row["new"]:
            if e["replaces"] in entries:
                entries[e["replaces"]] = {**entries[e["replaces"]], "active": False}
            entries[e["id"]] = e
    return rec.SessionRecord(tuple(rec.Entry(**e) for e in entries.values()))


def _verdict(log):
    return {name: ok for name, ok, _ in _load_script()._verdict(_record(log), log)}


def test_clean_log_passes_every_line():
    assert all(_verdict(_clean_log()).values())


def test_each_known_failure_trips_its_line():
    log = _clean_log()
    log[0]["new"] = []                                                    # 种子没记到
    assert not _verdict(log)["每条种子都记到"]

    log = _clean_log()
    log[3]["new"] = [_e(3, "plans", "对方改去金陵", "改去金陵")]           # 改口没用取代
    assert not _verdict(log)["每处改口都用取代完成"]

    log = _clean_log()
    log[3]["new"].append(_e(4, "plans", "对方去金陵", "改去金陵"))          # 取代之外又记一遍（上一场 #14、#15）
    assert not _verdict(log)["改口那一轮没有重复记"]

    log = _clean_log()
    log[2]["new"] = [_e(9, "known", f"对方问起{i}", f"问{i}") for i in range(4)]   # 填充轮乱记「了解」
    assert not _verdict(log)["填充轮新增「了解」≤ 3"]

    log = _clean_log()
    log[2]["new"] = [_e(9, "relation", "嘴上撵人，心里已不设防", "x", source="character")]
    assert not _verdict(log)["没有独白腔条目"]

    log = _clean_log()
    log[2]["new"] = [_e(10 + i, "plans", "很长的计划" * 40, f"e{i}") for i in range(6)]
    assert not _verdict(log)["注入块 ≤ 768 token"]
