"""D25 第 1 步目标检查：同一用户可同时蒸多个角色，同一（书,角色）只允许一个。

B1–B4、B7 是实现前 ``xfail(strict=True)`` 的目标行；B5、B6 是对照（现状即绿）。
夹具与替身全部复用 ``test_distill_task_api``。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

import deps
import test_distill_task_api as A
from test_distill_task_api import store, user_id, _clean_tasks  # noqa: F401  复用同一套夹具
from routers import distill as D

pytestmark = pytest.mark.usefixtures("embedding_key_configured")


def _text(store, uid, body="角色说的话"):
    tid = f"txt_{uuid.uuid4().hex}"
    A._run_async(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


@pytest.mark.xfail(strict=True, reason="D25 第 1 步：每用户上限改 3，三条串行都该 200")
def test_b1_three_serial_starts_same_user_all_200(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    for name in ("甲", "乙", "丙"):
        resp, _ = A._capture_start(monkeypatch, store, user_id, tid, character=name)
        assert resp.status_code == 200, resp.text


@pytest.mark.xfail(strict=True, reason="D25 第 1 步：第 4 个任务撞每用户上限 3")
def test_b2_fourth_start_rejected_with_limit_message(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    for name in ("甲", "乙", "丙"):
        resp, _ = A._capture_start(monkeypatch, store, user_id, tid, character=name)
        assert resp.status_code == 200, resp.text

    fourth, _ = A._capture_start(monkeypatch, store, user_id, tid, character="丁")
    assert fourth.status_code == 429, fourth.text
    assert "最多 3 个" in fourth.json()["detail"]


@pytest.mark.xfail(strict=True, reason="D25 第 1 步：同一（书,角色）第二次起要按目标拦")
def test_b3_same_target_second_start_rejected(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    first, _ = A._capture_start(monkeypatch, store, user_id, tid, character="甲")
    assert first.status_code == 200, first.text

    second, _ = A._capture_start(monkeypatch, store, user_id, tid, character="甲")
    assert second.status_code == 429, second.text
    assert "这个角色正在蒸馏中" in second.json()["detail"]


@pytest.mark.xfail(strict=True, reason="D25 第 1 步：并发起不同角色都该放行")
async def test_b4_concurrent_different_targets_both_start(store, user_id, monkeypatch):
    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=user_id)
    distiller, _sem, threads, llm = A._install_bg(monkeypatch, store)
    # 前提断言用 async 语境自己的驱动方式（同 test_concurrent_double_start_same_target）
    assert await deps.get_user_llm("premise", store) is llm, (
        "前提破了：解析出口没给出实例 —— /start 会以无 key 503 收场，测的就不是并发闸")

    transport = ASGITransport(app=A._build_app(store, user_id))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1, r2 = await asyncio.gather(
            ac.post("/api/distill/start",
                    json={"text_id": tid, "character_name": "甲", "force": False}),
            ac.post("/api/distill/start",
                    json={"text_id": tid, "character_name": "乙", "force": False}),
        )
    for t in threads:
        t.join(timeout=30)

    assert sorted([r1.status_code, r2.status_code]) == [200, 200], (r1.text, r2.text)
    assert len(threads) == 2, "两个不同角色应各起一条后台线程"


def test_b5_concurrent_same_target_still_single_thread(store, user_id, monkeypatch):
    """对照：同一（书,角色）并发双 /start 仍只放行一个。"""
    tid = f"txt_{uuid.uuid4().hex}"
    A._run_async(store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=user_id))
    distiller, _sem, threads, llm = A._install_bg(monkeypatch, store)
    A._assert_premise_resolves(store, llm)

    async def _both():
        transport = ASGITransport(app=A._build_app(store, user_id))
        payload = {"text_id": tid, "character_name": "甲", "force": False}
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await asyncio.gather(
                ac.post("/api/distill/start", json=payload),
                ac.post("/api/distill/start", json=payload),
            )

    r1, r2 = A._run_async(_both())
    for t in threads:
        t.join(timeout=30)

    assert sorted([r1.status_code, r2.status_code]) == [200, 429], (r1.text, r2.text)
    assert len(threads) == 1, "同一目标只该起一条后台线程"


def test_b6_different_users_same_target_both_start(store, monkeypatch):
    """对照：不同用户的同一（书,角色）互不干扰。"""
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    uid_b = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a = _text(store, uid_a)
    tid_b = _text(store, uid_b)

    ra, _ = A._capture_start(monkeypatch, store, uid_a, tid_a, character="甲")
    rb, _ = A._capture_start(monkeypatch, store, uid_b, tid_b, character="甲")
    assert ra.status_code == 200, ra.text
    assert rb.status_code == 200, rb.text


@pytest.mark.xfail(strict=True, reason="D25 第 1 步：释放带目标后同一目标可再次预留")
def test_b7_release_target_allows_reserve_again():
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    assert D._reserve_user_slot(uid, ("t", "甲")) is None
    D._release_user_slot(uid, ("t", "甲"))
    with D._task_lock:
        assert uid not in D._user_slots
    assert D._reserve_user_slot(uid, ("t", "甲")) is None
    D._release_user_slot(uid, ("t", "甲"))
