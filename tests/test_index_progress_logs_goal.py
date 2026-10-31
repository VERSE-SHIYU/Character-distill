# -*- coding: utf-8 -*-
"""目标检查：建索引的两条完成日志必须是 WARNING，生产日志才看得见。

生产根日志器与唯一 stdout handler 都是 WARNING（core/stdout_logging.py:35），
`logger.info` 在生产被丢弃。故这里断言的是**级别**与**真的写到 stdout**，
不是「这段文本出现在某个捕获器里」—— 后者在 INFO 下也成立，守不住目标。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from core import indexing_service
from core.indexing_service import IndexingService, _run_job
from core.stdout_logging import install_stdout_logging

BUILT = "RAG text collection built"
DONE = "done in"


class _BuildRagEngine:
    """照生产行为的最小替身：集合不存在 → 整本建一次，不出网、不碰 chroma。"""

    def __init__(self, config):
        self.config = config
        self.collection = None
        self.collection_name = None

    def load_existing(self, name) -> bool:
        return False

    def index(self, *_a, collection_name=None, **_kw) -> None:
        self.collection_name = collection_name


def _build_once() -> None:
    IndexingService({})._build_text_collection(
        "t1", "正文", None, embedding_key="sk-test", embedding_region="cn", rebuild=True,
    )


def _job_once() -> None:
    asyncio.run(_run_job("Scene index card_id=c1", lambda: None, ()))


def _records(caplog, needle: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if needle in r.getMessage()]


@pytest.mark.xfail(strict=True, reason="INFO 不进生产日志；目标：两条都变 WARNING")
def test_index_build_logs_are_warning(monkeypatch, caplog):
    monkeypatch.setattr(indexing_service, "RAGEngine", _BuildRagEngine)
    with caplog.at_level(logging.DEBUG, logger="core.indexing_service"):
        _build_once()
        _job_once()
    built, done = _records(caplog, BUILT), _records(caplog, DONE)
    assert built and done, "两处完成日志各应执行一次"
    assert built[0].levelno == logging.WARNING, built[0].levelname
    assert done[0].levelno == logging.WARNING, done[0].levelname


@pytest.mark.xfail(strict=True, reason="INFO 被 WARNING handler 丢弃；目标：两条都写到 stdout")
def test_index_build_logs_reach_stdout(monkeypatch, capsys):
    monkeypatch.setattr(indexing_service, "RAGEngine", _BuildRagEngine)
    # capsys 在用例开始时已生效（handler 读时取 sys.stdout），这里才装，顺序不能反。
    undo = install_stdout_logging()
    try:
        _build_once()
        _job_once()
        out = capsys.readouterr().out
    finally:
        undo()
    assert BUILT in out, out
    assert DONE in out, out
