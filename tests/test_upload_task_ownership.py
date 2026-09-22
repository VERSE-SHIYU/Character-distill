# -*- coding: utf-8 -*-
"""上传任务状态归属校验：修复前任何登录用户拿 task_id 就能读别人的上传任务。

条目此前只存 status / progress_pct / message / text_id，属主无从校验，函数体拿到 user
也从不引用。本测试锁：非属主与不存在同判 404（同一条文案，不可区分）、条目缺 user_id
也拒（fail closed）、属主能读、返回体不把 user_id 带上屏。

为什么是 404 不是 403：403 说「任务存在但你没权限」，攻击者拿一批 task_id 扫描、靠
403/404 差异就能枚举出哪些真实存在；task_id 是 uuid，枚举出存在性即缩小爆破面。见
AGENTS.md §四「授权失败一律 404」，voice.py preview_audio / voice_synthesize 同口径。
"""
import asyncio

import pytest
from fastapi import HTTPException

import routers.text as M


@pytest.fixture(autouse=True)
def _clean_tasks():
    with M._upload_task_lock:
        M._upload_tasks.clear()
    yield
    with M._upload_task_lock:
        M._upload_tasks.clear()


def _put(task_id, **fields):
    with M._upload_task_lock:
        M._upload_tasks[task_id] = fields


def test_foreign_user_is_denied():
    _put("t1", status="parsing", text_id="tx", user_id="u1")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(M.get_upload_task_status("t1", user={"id": "u2"}))
    assert ei.value.status_code != 403, "返 403 可区分存在性"
    assert ei.value.status_code == 404


def test_task_without_owner_is_denied():
    """fail closed：条目缺 user_id 也拒，不因为「无从校验」就放行。"""
    _put("t1", status="parsing", text_id="tx")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(M.get_upload_task_status("t1", user={"id": "u1"}))
    assert ei.value.status_code != 403, "返 403 可区分存在性"
    assert ei.value.status_code == 404


def test_owner_reads_and_user_id_is_not_returned():
    _put("t1", status="parsing", text_id="tx", user_id="u1")
    result = asyncio.run(M.get_upload_task_status("t1", user={"id": "u1"}))
    assert result["status"] == "parsing"
    assert result["text_id"] == "tx"
    assert "user_id" not in result


def test_missing_task_is_404():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(M.get_upload_task_status("nope", user={"id": "u1"}))
    assert ei.value.status_code == 404


def test_foreign_and_missing_are_indistinguishable():
    """非属主与不存在必须同码同文案——否则 403/404 差异本身就是存在性预言机。"""
    _put("t1", status="parsing", text_id="tx", user_id="u1")
    with pytest.raises(HTTPException) as ei_foreign:
        asyncio.run(M.get_upload_task_status("t1", user={"id": "u2"}))
    with pytest.raises(HTTPException) as ei_missing:
        asyncio.run(M.get_upload_task_status("nope", user={"id": "u2"}))
    assert ei_foreign.value.status_code == ei_missing.value.status_code == 404
    assert ei_foreign.value.detail == ei_missing.value.detail


def test_failure_message_is_generic_and_the_log_keeps_the_raw_text(monkeypatch, capsys):
    """缺陷 94（泄漏那半）：任务状态给通用文案，上游原文只留 print 那句日志。

    收走原文 ≠ 扔掉原文 —— 上屏那句不许含内部标识，日志里必须还有。
    """
    raw = "RuntimeError: upstream 500 boom at adapters/llm_adapter.py:200"

    def _boom(coro):
        coro.close()          # 别留一个从未 await 的协程（RuntimeWarning 会污染别的用例）
        raise RuntimeError(raw)

    monkeypatch.setattr(M, "submit_to_main_loop", _boom)

    M._run_upload_task("t-94", "text-1", "u1")

    with M._upload_task_lock:
        task = dict(M._upload_tasks["t-94"])
    assert task["status"] == "error"
    assert task["message"] == "服务暂时不可用，请稍后重试"
    assert raw not in task["message"]
    assert raw in capsys.readouterr().out, "原文被收走的同时也从日志里丢了"
