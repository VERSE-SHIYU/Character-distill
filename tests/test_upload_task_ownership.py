# -*- coding: utf-8 -*-
"""上传任务状态归属校验：修复前任何登录用户拿 task_id 就能读别人的上传任务。

条目此前只存 status / progress_pct / message / text_id，属主无从校验，函数体拿到 user
也从不引用。本测试锁：非属主 403、条目缺 user_id 也拒（fail closed）、属主能读、
返回体不把 user_id 带上屏。
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
    assert ei.value.status_code == 403


def test_task_without_owner_is_denied():
    """fail closed：条目缺 user_id 也拒，不因为「无从校验」就放行。"""
    _put("t1", status="parsing", text_id="tx")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(M.get_upload_task_status("t1", user={"id": "u1"}))
    assert ei.value.status_code == 403


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
