# -*- coding: utf-8 -*-
"""目标检查：重放工具用**属主自己的** LLM，不用全局。

生产只有一条出站解析出口 `web/deps.py:136-150` `get_user_llm(user_id)`；全局那条路
（`get_llm()`）在 SG 上不存在（容器无 key）。工具与生产同一出口，且属主没配时直接退出。
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_replay_distill_goal import _NAME, _prepare, _stub

#: 实现提交里删除本行；strict 保证「忘了删」当场红。
pytestmark = pytest.mark.xfail(strict=True, reason="实现前：工具仍取全局 LLM")


def _args(text_id: str, out: str):
    import scripts.replay_distill as replay
    return replay._parse_args(
        ["--text-id", text_id, "--name", _NAME, "--out", out])


def test_tool_uses_owner_llm(monkeypatch, tmp_path):
    """属主有配置：用的就是 `get_user_llm(属主)` 返回的实例，`get_llm` 一次没调。"""
    import scripts.replay_distill as replay

    stub = _stub()
    seen: list[tuple[str, object]] = []
    global_calls: list[int] = []

    async def fake_user_llm(user_id, storage=None):
        seen.append((user_id, storage))
        return stub

    def forbidden_global_llm():
        global_calls.append(1)
        raise AssertionError("工具不得再走 get_llm()")

    monkeypatch.setattr("web.deps.get_user_llm", fake_user_llm)
    monkeypatch.setattr("web.deps.get_llm", forbidden_global_llm)

    async def go():
        store, user, text = await _prepare()
        monkeypatch.setattr("web.deps.get_storage", lambda: store)
        await replay._main(_args(text["id"], str(tmp_path)))
        return store, user

    store, user = asyncio.run(go())

    assert [uid for uid, _ in seen] == [user["id"]], seen
    assert seen[0][1] is store, "应把引导读用的同一个 storage 交给解析出口"
    assert stub._stub_invoked, "工具没有真正使用 get_user_llm 返回的实例"
    assert global_calls == [], "get_llm() 被调用了"
    assert (tmp_path / "summary.json").exists()


def test_tool_exits_when_owner_has_no_llm(monkeypatch, tmp_path):
    """属主没配置：非零退出，`--out` 下不产生任何运行产物，不发 LLM 调用。"""
    import scripts.replay_distill as replay

    out = tmp_path / "out"
    global_calls: list[int] = []

    async def none_llm(user_id, storage=None):
        return None

    def forbidden_global_llm():
        global_calls.append(1)
        raise AssertionError("工具不得回落全局 LLM")

    monkeypatch.setattr("web.deps.get_user_llm", none_llm)
    monkeypatch.setattr("web.deps.get_llm", forbidden_global_llm)

    async def go():
        store, _user, text = await _prepare()
        monkeypatch.setattr("web.deps.get_storage", lambda: store)
        with pytest.raises(SystemExit) as exc:
            await replay._main(_args(text["id"], str(out)))
        return exc.value

    error = asyncio.run(go())

    assert error.code != 0, error.code
    assert "属主未配置 LLM" in str(error.code), error.code
    assert global_calls == [], "get_llm() 被调用了"
    assert not out.exists(), list(out.iterdir()) if out.exists() else None
