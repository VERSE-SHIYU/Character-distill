# -*- coding: utf-8 -*-
"""chat SSE 错误帧契约：未完成终态必须可识别（code + finish_reason），且不把运维口径
和内部 where 标识漏给终端用户；其余异常保持原样。

前端 client.js 靠 payload.error 展示、靠 payload.code 区分——这条契约断了，
用户看到的就会退回「连接中断」或一句暴露内部标识的乱码。
"""
from __future__ import annotations

from adapters.llm_adapter import IncompleteResponseError
from routers.chat import _stream_error_payload


def test_incomplete_payload_is_identifiable():
    p = _stream_error_payload(IncompleteResponseError("length", "chat_stream"))
    assert p["code"] == "incomplete_response"
    assert p["finish_reason"] == "length"


def test_incomplete_payload_is_user_facing():
    p = _stream_error_payload(IncompleteResponseError("content_filter", "chat_stream"))
    assert p["error"] == "内容被安全策略拦截，请修改后重试"
    assert "chat_stream" not in p["error"]          # 内部 where 标识不上屏
    assert "max_tokens" not in p["error"]           # 运维口径不上屏


def test_other_errors_keep_original_shape():
    p = _stream_error_payload(RuntimeError("LLM API failed after 3 attempts"))
    assert p == {"error": "LLM API failed after 3 attempts"}
    assert "code" not in p
