"""目标检查：评测里的检索失败，日志与证据产物都看得见、对得上是哪个角色。

三条断言各锁一个「达成目标」：

1. 不走路由（``_score_query``）的检索失败 WARNING 同时含题号与角色名。
2. 走路由（``_score_via_router``）同上 —— 套件一次跑多本，题号跨书重复，只有角色名能定位。
3. 证据产物保留 ``retrieval_failed`` 计数，且不被记为脱敏字段；落点被隔离到 tmp，
   仓库里的 ``docs/evidence/`` 一个字节都不动。

实现前这些断言不成立 → 模块级 ``xfail(strict=True)``；实现落地后去掉标记，必须真绿。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "tests" / "perf") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "perf"))

from core.rag import CollectionUnusableError  # noqa: E402
from test_agent_loop import FakeLLM, FakeMessage, FakeToolCall  # noqa: E402

CHARACTER = "祈灼"
SOURCE = "满月宴那天祈灼在廊下听见了那支曲子"
CASE = {"id": "q35", "phase": None, "query": "你还记得满月宴那天吗",
        "should": [], "should_not": []}


class _BrokenRag:
    """取候选即抛 ``CollectionUnusableError`` —— 与 ``core/rag.py`` 的抛出点同一种异常。"""

    def chunk_spans(self, source_norm_len):
        return {}

    def query_scenes(self, query, **kwargs):
        raise CollectionUnusableError("向量检索查询失败：模拟集合不可用")


def _warning_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.xfail(strict=True, reason="尚未实现：失败日志还不带角色名")
def test_direct_path_warning_names_character(caplog):
    from scripts.eval_recall import _score_query

    caplog.set_level(logging.WARNING)
    row = _score_query(_BrokenRag(), SOURCE, CASE, {},
                       character_name=CHARACTER, window=None)

    assert row["retrieval_failed"] is True
    messages = _warning_messages(caplog)
    assert any(CASE["id"] in m and CHARACTER in m for m in messages), (
        f"检索失败日志没有同时带上题号与角色名：{messages}")


@pytest.mark.xfail(strict=True, reason="尚未实现：失败日志还不带角色名")
def test_router_path_warning_names_character(caplog):
    from scripts.eval_recall import _score_via_router

    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeToolCall("search_scenes", {"query": "满月宴"})]),
        FakeMessage(content="回答"),
    ])
    caplog.set_level(logging.WARNING)
    row = _score_via_router(_BrokenRag(), SOURCE, CASE, {},
                            character_name=CHARACTER, window=None, llm=llm)

    assert row["retrieval_failed"] is True
    messages = _warning_messages(caplog)
    assert any(CASE["id"] in m and CHARACTER in m for m in messages), (
        f"检索失败日志没有同时带上题号与角色名：{messages}")


@pytest.mark.xfail(strict=True, reason="尚未实现：retrieval_failed 不在证据白名单里")
def test_evidence_keeps_retrieval_failed(tmp_path, monkeypatch):
    import evidence_writer

    repo_evidence = ROOT / "docs" / "evidence"
    repo_manifest = repo_evidence / "manifest.json"
    repo_artifact = repo_evidence / "recall-baseline-qizhuo.json"
    before_manifest = repo_manifest.read_bytes() if repo_manifest.exists() else None
    before_artifact = repo_artifact.read_bytes() if repo_artifact.exists() else None

    monkeypatch.setattr(evidence_writer, "ROOT", tmp_path)
    monkeypatch.setattr(evidence_writer, "EVIDENCE_DIR", tmp_path / "docs" / "evidence")
    monkeypatch.setattr(evidence_writer, "MANIFEST",
                        tmp_path / "docs" / "evidence" / "manifest.json")

    payload = {"r_at_3": 0.5, "leak_rate": 0.1, "n": 10,
               "retrieval_failed": 2, "per_query": []}
    artifact = evidence_writer.write_evidence(
        "recall-baseline-qizhuo", payload,
        claim="R@3 0.50（n=10）", script="scripts/eval_recall.py",
        env="test", code_sha="deadbee")

    body = json.loads(artifact.read_text(encoding="utf-8"))
    assert body["retrieval_failed"] == 2, "证据产物丢了检索失败计数"

    manifest = json.loads(
        (tmp_path / "docs" / "evidence" / "manifest.json").read_text(encoding="utf-8"))
    entry = next(e for e in manifest if e["id"] == "recall-baseline-qizhuo")
    assert "retrieval_failed" not in entry["redacted_fields"], (
        f"计数不该被当成脱敏字段：{entry['redacted_fields']}")

    after_manifest = repo_manifest.read_bytes() if repo_manifest.exists() else None
    after_artifact = repo_artifact.read_bytes() if repo_artifact.exists() else None
    assert (after_manifest, after_artifact) == (before_manifest, before_artifact), (
        "证据落点没有隔离到 tmp：仓库里的 docs/evidence/ 被改动了")
