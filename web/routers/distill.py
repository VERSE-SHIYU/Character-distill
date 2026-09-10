"""Distillation: identify characters and generate character cards."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid as _uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from deps import get_indexing_service, get_sessions, get_storage, run_on_main_loop
from core.distiller import Distiller, text_fingerprint
from core.export import export_tavern_json
from core.schema import CharacterCard
from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）
from storage.base import StorageBase
from limiter import get_client_ip, limiter
from routers.auth import get_current_user


def _get_distill_content(text_rec: dict) -> str:
    """默认返回原文蒸馏，仅当 DISTILL_USE_COREF=1 时走共指消解版。"""
    if os.getenv("DISTILL_USE_COREF") == "1":
        resolved = text_rec.get("content_resolved", "")
        if resolved and text_rec.get("coref_resolved"):
            return resolved
    return text_rec["content"]


router = APIRouter(prefix="/api/distill", tags=["distill"])
legacy_router = APIRouter(tags=["legacy-distill"])


# ---- Request models ----

class IdentifyByIdRequest(BaseModel):
    """New: identify from a stored text by text_id."""
    text_id: str


class DistillByIdRequest(BaseModel):
    """New: distill from a stored text by text_id."""
    text_id: str
    character_name: str = ""
    force: bool = False


class StartSessionRequest(BaseModel):
    """Create a chat session for an existing card without re-distilling."""
    text_id: str = ""
    card_id: str
    user_role: str = ""
    client_tz: str = ""


class IdentifyRequest(BaseModel):
    """Legacy: identify from raw text."""
    text: str


class DistillRequest(BaseModel):
    """Legacy: distill from raw text."""
    text: str
    character_name: str = ""


# ---- Background task store ----

_tasks: dict[str, dict[str, Any]] = {}
_task_lock = threading.Lock()
DISTILL_MAX_CONCURRENT = 3
_DISTILL_SEMAPHORE = threading.Semaphore(DISTILL_MAX_CONCURRENT)  # 最多同时3个蒸馏任务

# ── 按用户并发闸 ──────────────────────────────────────────────────────────
# 每用户同时只允许 DISTILL_MAX_PER_USER 个蒸馏。两道门各司其职：
#   1. 预留位 _user_slots（同步、进程内）—— 挡并发双 /start 的 TOCTOU。count 与
#      insert 都隔着 await，同一 event loop 上两个请求会交错读到「该用户没在跑」
#      再各插一行、各起一条线程，同一批分片烧两遍额度。同步占坑是唯一裁决点。
#   2. DB 复核 count_running_distills（带时效窗）—— 挡跨重启残留的 running 行。
# 天花板：预留位是**进程内**的。当前部署单进程（Dockerfile CMD `python -m web.server`
# → uvicorn.run 未传 workers=），所以它就是权威。一旦上多 worker / 多副本，必须升级
# 为 distill_tasks 上的部分唯一索引 (user_id) WHERE status IN ('queued','running')
# —— 只有 DB 约束才是跨进程真原子。
DISTILL_MAX_PER_USER = 1
# 幽灵行时效窗：活任务的进度写会持续刷新 updated_at，running 行超这么久没刷新即视为
# 「线程已死、终态没落库」，不再计入占用 —— 否则该用户被一行死任务永久挡住且无法自救。
DISTILL_GHOST_IDLE_MIN = int(os.getenv("DISTILL_GHOST_IDLE_MIN", "30"))
_DISTILL_BUSY_MSG = (
    f"你已有蒸馏任务在运行（每用户同时最多 {DISTILL_MAX_PER_USER} 个），"
    "请等它完成或停止后再试"
)
_user_slots: dict[str, int] = {}


def _reserve_user_slot(user_id: str) -> bool:
    """同步占一个用户槽位；满了返回 False。纯同步无 await —— 并发双 /start 的裁决点。"""
    with _task_lock:
        if _user_slots.get(user_id, 0) >= DISTILL_MAX_PER_USER:
            return False
        _user_slots[user_id] = _user_slots.get(user_id, 0) + 1
        return True


def _release_user_slot(user_id: str) -> None:
    """释放一个用户槽位；未占坑的用户是 no-op（不产生负数）。"""
    with _task_lock:
        n = _user_slots.get(user_id, 0) - 1
        if n > 0:
            _user_slots[user_id] = n
        else:
            _user_slots.pop(user_id, None)


# ── DB 真相源 / 内存写缓存 ─────────────────────────────────────────────────
# 任务状态查询一律读 distill_tasks 表；内存 _tasks 只在 bg 线程里作写侧缓存
# （_set_task 先更内存、再 UPDATE 落库 —— update-only，行没了写不回去）。跨重启：
# boot reconcile 把孤儿 running 置 interrupted（web/server.py _lifespan），供步骤 3 断点续跑挑选。
_DB_TERMINAL = {"done", "error", "interrupted"}


def _db_status(mem_status: str) -> str:
    """内存 rich 状态 → DB 四态。done/error/interrupted 原样，其余(含 queued)→ running。

    排队/识别/分析/合并/格式化/保存都是「没结束、占一个 worker」，DB 统一记
    running 作跨重启决策；rich 细节只给前端看、存内存就够。
    """
    if mem_status in _DB_TERMINAL:
        return mem_status
    return "running"


async def _persist_snap(task_id: str, snap: dict[str, Any]) -> None:
    """落库 + 成功后记账 + 终态清理。main loop 线程执行（由 run_on_main_loop 派发）。

    记账(_db)放在持久化成功之后：失败时 _db 保持旧值，下一次同状态调用不会被去重吞
    掉，能自动补发。终态成功落库 = bg 线程最后一次写，此后再无 update —— pop 写缓存
    防无界累积（cancel 路由不经过这里、直接 update DB，不会误清仍在跑的停止信号）。
    """
    # UPDATE-only：行不在（文本被删、行已清）→ 0 行、静默无操作。这正是竞态闭合点 ——
    # bg 线程无需知道文本被删，写不回去是存储层的保证。
    await get_storage().update_distill_task(
        task_id,
        status=snap["status"], progress_pct=snap["progress_pct"],
        message=snap["message"], card_id=snap["card_id"], awakening=snap["awakening"],
    )
    with _task_lock:
        cur = _tasks.get(task_id)
        if cur is not None:
            cur["_db"] = (snap["status"], snap["progress_pct"])
            if snap["status"] in _DB_TERMINAL:
                _tasks.pop(task_id, None)


def _dispatch_persist(task_id: str, snap: dict[str, Any]) -> None:
    """bg 线程把一次状态写派发到 main loop 落库。失败打日志（non-fatal），不抛异常。"""
    try:
        run_on_main_loop(_persist_snap(task_id, snap), timeout=10)
    except Exception as exc:
        # 进度记录写失败不致命：蒸馏照常，下次状态变更再追上
        print(f"[distill] Persist task {task_id} state failed (non-fatal): {exc}")


def _set_task(task_id: str, updates: dict[str, Any]) -> None:
    """内存写缓存更新 + DB UPDATE 落库（update-only）。仅 bg 线程调用（内部 run_on_main_loop 派发）。

    去重键 = (粗粒度 status, progress_pct)：analyze/merging 每 chunk 只动
    message/current/total 时不再发 DB；终态或带 card_id/awakening 的收尾更新总落库。
    _db 只在落库成功后更新（见 _persist_snap）——记账不提前于事实，DB 没收到就不认。
    """
    with _task_lock:
        task = _tasks.get(task_id)
        if task is None:
            return  # 条目不存在（不应发生在活跃任务），不复活
        task.update(updates)
        st = _db_status(task.get("status", "queued"))
        pct = int(task.get("progress_pct", 0) or 0)
        force = st in _DB_TERMINAL or "card_id" in updates or "awakening" in updates
        if task.get("_db") == (st, pct) and not force:
            return
        snap = {
            "user_id": task.get("user_id", ""),
            "text_id": task.get("text_id", ""),
            "character": task.get("character", ""),
            "status": st,
            "progress_pct": pct,
            "message": task.get("message", ""),
            "card_id": task.get("card_id", ""),
            "awakening": task.get("awakening", ""),
        }
    _dispatch_persist(task_id, snap)


def _confirm_terminal_persist(task_id: str) -> None:
    """终态收口：bg 线程退出前补一次确认写，防 DB 行永久停 running。

    在 _run_distill_task 的 finally（及 acquire 失败分支）调用，此刻 bg 线程已无别的
    写者。只有当内存条目仍带终态且 _db 未确认（即此前那次终态写失败）才补写；成功后
    照常 pop。重试仍失败：打区别于普通 non-fatal 的日志，提示 boot reconcile 兜底；
    不 pop（保持内存可见供查询 stage）、不抛异常影响 semaphore release。
    """
    with _task_lock:
        task = _tasks.get(task_id)
        if task is None:
            return  # 终态已成功落库并 pop，无需补写
        st = _db_status(task.get("status", "queued"))
        if st not in _DB_TERMINAL:
            return  # 理论不进 finally 的非终态，走正常 _set_task
        pct = int(task.get("progress_pct", 0) or 0)
        if task.get("_db") == (st, pct):
            return  # 已确认落库（如 pop 未执行的边缘），避免重复写
        snap = {
            "user_id": task.get("user_id", ""),
            "text_id": task.get("text_id", ""),
            "character": task.get("character", ""),
            "status": st,
            "progress_pct": pct,
            "message": task.get("message", ""),
            "card_id": task.get("card_id", ""),
            "awakening": task.get("awakening", ""),
        }
    try:
        run_on_main_loop(_persist_snap(task_id, snap), timeout=10)
    except Exception as exc:
        # 区别于普通 non-fatal：这是终态确认的第二次失败，DB 行可能滞留 running 占
        # count_running 槽，只能靠下次 boot reconcile 置 interrupted。
        print(f"[distill] Task {task_id} TERMINAL persist failed in final confirm: {exc}. "
              f"DB row may stay running; boot reconcile will flip it to interrupted.")


class DistillTaskRequest(BaseModel):
    text_id: str
    character_name: str = ""
    force: bool = False


async def cancel_distill_tasks_by_text_id(text_id: str) -> int:
    """Cancel all in-flight distill tasks matching the given text_id.

    Loop 线程调用（text.py 软/硬删路由）。内存 loop 只为让 bg 线程收到停止信号，
    DB 扫一遍同 text_id 的活跃行（含上一个进程的孤儿）统一置 error —— 否则删掉的
    文本会留一根 running 孤儿行永远占着 count_running 槽。
    Returns the number of DB rows cancelled.
    """
    with _task_lock:
        for tid, task in list(_tasks.items()):
            if task.get("text_id") == text_id and task.get("status") not in ("done", "error"):
                task.update({"status": "error", "message": "文本已删除，任务已取消"})
    try:
        return await get_storage().cancel_distills_by_text_id(text_id, "文本已删除，任务已取消")
    except Exception as exc:
        print(f"[distill] DB cancel by text failed (non-fatal): {exc}")
        return 0


def _generate_awakening(llm, card: CharacterCard) -> str:
    """Generate an awakening line for a newly distilled character.

    Returns the line text, or empty string on any failure.
    Never raises — all exceptions are caught and logged.
    """
    if llm is None or not card.first_message:
        return ""
    try:
        style = card.speaking_style
        prompt = (
            f"你现在是「{card.name}」。你刚从长梦中醒来，第一眼认出了眼前的人。\n"
            f"你的身份：{card.identity}\n"
            f"你的语气：{style.tone}\n\n"
            f"你的原开场白是：「{card.first_message}」\n\n"
            f"请基于原开场白的口吻，说一句「刚从长梦中醒来、第一眼认出眼前人」的话"
            f"——带一点初醒的朦胧和「是你啊」的温度。\n"
            f"不是重写开场白，而是原口吻的变形。只输出这句话本身，不要引号，不要解释，不超过50个字。"
        )
        result = llm.chat(prompt, [{"role": "user", "content": "请说苏醒台词"}])
        result = result.strip().strip('"').strip("'").strip("「」").strip("《》")
        if not result or len(result) > 100:
            return ""
        return result
    except Exception as exc:
        print(f"[distill] Generate awakening failed (non-fatal): {exc}")
        return ""


@T.spanned("distill.task", wf="distill")
def _run_distill_task(
    task_id: str, text_id: str, char_name: str, force: bool, user_id: str,
    content: str, text_type: str, api_config: dict | None = None,
    client_ip: str | None = None, resume_candidates: dict[int, dict] | None = None,
) -> None:
    """Background thread: run distillation end-to-end, update _tasks[task_id].

    All DB I/O is dispatched back to the main event loop via
    ``run_on_main_loop()``, which uses ``run_coroutine_threadsafe`` so the
    asyncpg pool is only ever touched from the loop that created it.
    LLM calls (slow, network-heavy) stay on this background thread.

    ``resume_candidates`` = {chunk_index: {"result", "fingerprint"}} from a prior
    interrupted run (see /start); distiller reuses hits and reruns the rest.
    """
    acquired = _DISTILL_SEMAPHORE.acquire(timeout=300)
    if not acquired:
        print(f"[distill] 并发蒸馏达上限，任务超时: {char_name}")
        _set_task(task_id, {"status": "error", "message": "服务器繁忙，请稍后重试"})
        _confirm_terminal_persist(task_id)
        _release_user_slot(user_id)   # 没进 try/finally，按用户槽要在这里放
        return
    try:
        from adapters.llm_adapter import LLMAdapter
        from deps import get_distiller, get_text_manager

        per_user_llm = None
        if api_config and api_config.get("api_key"):
            try:
                if client_ip is not None:
                    from web.geo_guard import check_api_allowed
                    allowed, _ = check_api_allowed(client_ip, api_config.get("base_url", "https://api.deepseek.com"))
                    if not allowed:
                        print(f"[distill] Geo-blocked per-user LLM in bg task for {user_id}")
                        raise RuntimeError("geo_blocked")
                from adapters.llm_adapter import LLMAdapter
                per_user_llm = LLMAdapter(
                    api_key=api_config["api_key"],
                    base_url=api_config.get("base_url", "https://api.deepseek.com"),
                    model=api_config.get("model", "deepseek-v4-pro"),
                )
            except Exception as exc:
                print(f"[distill] Per-user LLM init failed, falling back: {exc}")

        distiller = get_distiller(llm=per_user_llm)
        text_manager = get_text_manager(llm=per_user_llm)

        if not distiller or not text_manager:
            _set_task(task_id, {"status": "error", "message": "请先在设置页配置 API Key"})
            return

        # Step 1: resolve character name + aliases in ONE LLM call
        name = char_name.strip()
        aliases: list[str] = []

        _set_task(task_id, {"status": "identifying", "progress_pct": 5, "character": name or char_name, "message": "正在读取文本…"})
        _set_task(task_id, {"progress_pct": 8, "message": "正在识别角色…"})

        try:
            chars = distiller.identify_characters(content)
        except Exception:
            chars = []
        if not name:
            if not chars:
                _set_task(task_id, {"status": "error", "message": "No characters identified"})
                return
            name = chars[0].get("name", "")
            if not name:
                _set_task(task_id, {"status": "error", "message": "Identified result missing name"})
                return
        for c in chars:
            if c.get("name") == name:
                aliases = c.get("aliases", [])
                break

        _set_task(task_id, {"status": "analyzing", "current": 0, "total": 0, "progress_pct": 10, "character": name, "message": "开始分析…"})

        # Step 2: run incremental distill (synchronous, collect full output)
        full = ""
        full_format = ""
        cur_phase = None
        EXPECT_CHARS = 3500

        def _persist_chunk(index: int, result: str, fingerprint: str) -> None:
            """每片 Map 完成即落库（ON CONFLICT DO NOTHING，重写同 index 是 no-op）。

            写失败不致命（该片退化为下次重跑），但不静默吞——续跑省调用靠的就是这些片。
            """
            async def _save() -> None:
                await get_storage().save_distill_chunk(task_id, index, result, fingerprint)
            try:
                run_on_main_loop(_save(), timeout=10)
            except Exception as exc:
                print(f"[distill] Persist chunk {index} of {task_id} failed (non-fatal): {exc}")

        stream = distiller.distill_incremental_stream(
            content, name, aliases, text_type,
            on_chunk_done=_persist_chunk, resume_candidates=resume_candidates,
        )
        for piece in stream:
            with _task_lock:
                aborted = _tasks.get(task_id, {}).get("status") == "error"
            if aborted:
                # cancel/删文本置了内存 error 作停止信号。这里补一个终态 DB 写，
                # 盖掉 cancel 之后 bg 可能交错的最后一次 running 进度写（避免 DB
                # 停在 running 占 count_running 槽）。message 从内存取已取消原因。
                _set_task(task_id, {"status": "error"})
                return
            if isinstance(piece, dict):
                if "error" in piece:
                    print(f"[distill] Stream error for {name}: {piece['error']}")
                    _set_task(task_id, {"status": "error", "message": piece["error"], "character": name})
                    return
                if piece.get("heartbeat"):
                    continue
                cur_phase = piece.get("status", cur_phase)
                current = piece.get("current", 0)
                total = piece.get("total", 1)
                status = piece.get("status", "analyzing")
                if status == "analyzing":
                    pct = 10 + int((current / total) * 60) if total > 0 else 10
                    msg = f"分析角色 {current}/{total}"
                elif status == "merging":
                    pct = 70 + int((current / total) * 20) if total > 0 else 75
                    msg = f"合并角色信息 {current}/{total}"
                elif status == "formatting":
                    full_format = ""
                    pct = 40
                    msg = "生成角色卡…"
                else:
                    pct = 10
                    msg = ""
                _set_task(task_id, {
                    "status": status,
                    "current": current,
                    "total": total,
                    "progress_pct": pct,
                    "character": name,
                    "message": msg,
                })
            else:
                if cur_phase == "formatting":
                    full_format += piece
                    f_len = len(full_format)
                    f_pct = 40 + min(int(f_len / EXPECT_CHARS * 55), 55)
                    _set_task(task_id, {"progress_pct": f_pct})
                full += piece

        # Step 3: parse + validate — 健壮处理 LLM 可能的格式问题
        stripped = full.strip()
        data = None

        import re
        # 1. 去掉 markdown 代码块标记
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', stripped, re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()

        # 2. 尝试直接解析
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            # 3. 提取最外层的 { ... }（处理前缀/后缀文字）
            brace_depth = 0
            json_start = -1
            json_end = -1
            for i, ch in enumerate(stripped):
                if ch == '{':
                    if brace_depth == 0:
                        json_start = i
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
                    if brace_depth == 0 and json_start != -1:
                        json_end = i
                        break  # 找到第一个完整的顶层 {}

            if json_start != -1 and json_end != -1:
                try:
                    data = json.loads(stripped[json_start:json_end + 1])
                except json.JSONDecodeError:
                    pass

        if data is None:
            if not stripped:
                print(f"[distill] Empty format output for {name} — Map/Reduce likely failed upstream")
                _set_task(task_id, {"status": "error", "message": "蒸馏过程异常，请查看服务器日志", "character": name})
            else:
                print(f"[distill] JSON parse failed for {name}. First 200 chars: {stripped[:200]}")
                _set_task(task_id, {"status": "error", "message": "蒸馏失败：LLM 返回格式不正确", "character": name})
            return

        from core.schema import CharacterCard
        try:
            card = CharacterCard.model_validate(data)
        except Exception as exc:
            _set_task(task_id, {"status": "error", "message": f"蒸馏失败：数据校验错误 {exc}", "character": name})
            return

        # AI auto-tagging (fails open)
        try:
            card_dict = card.model_dump()
            tags = distiller._auto_tag(card_dict)
            if tags:
                card_dict["tags"] = tags
                card = CharacterCard.model_validate(card_dict)
        except Exception as exc:
            print(f"[distill] Auto-tagging failed (silent): {exc}")

        # Step 4: persist via the main event loop (run_coroutine_threadsafe)
        # so the asyncpg pool stays on its home loop.
        async def _save_card():
            from deps import get_config, get_rag_config, get_sessions
            from core.text_manager import TextManager

            store = get_storage()
            llm_for_save = per_user_llm
            if llm_for_save is None:
                from deps import get_llm
                llm_for_save = get_llm()

            tm = TextManager(
                store,
                get_distiller(llm=llm_for_save),
                llm_for_save,
                get_rag_config(),
                get_sessions(),
                get_config().get("llm", {}).get("summary_threshold", 50),
            )
            _ek = (api_config or {}).get("embedding_key", "")
            _er = (api_config or {}).get("embedding_region", "")
            result = await tm.save_distilled_card(
                text_id, card, user_id,
                embedding_key=_ek, embedding_region=_er,
            )
            return result

        _set_task(task_id, {
            "status": "saving",
            "progress_pct": 95,
            "message": "正在保存角色卡…",
        })

        try:
            result = run_on_main_loop(_save_card(), timeout=120)
        except FutureTimeoutError:
            _set_task(task_id, {
                "status": "error",
                "message": "保存超时：角色卡写入耗时过长，请重试",
                "character": name, "text_id": text_id,
            })
            return
        print(f"[distill] Card saved: card_id={result.get('card_id','')} name={name} text_id={text_id} user_id={user_id}")

        # Generate awakening line (non-fatal, outside lock)
        awakening = _generate_awakening(per_user_llm, card)

        # Persist awakening_message to card (non-fatal)
        if awakening:
            try:
                card.awakening_message = awakening
                run_on_main_loop(
                    get_storage().update_card(result["card_id"], card.model_dump()),
                    timeout=30,
                )
                print(f"[distill] Persisted awakening_message to card {result['card_id']}")
            except Exception as exc:
                print(f"[distill] Persist awakening_message to card failed (non-fatal): {exc}")

        update_dict = {
            "status": "done",
            "card_id": result.get("card_id", ""),
            "character": name,
            "progress_pct": 100,
            "message": "蒸馏完成 ✓",
        }
        if awakening:
            update_dict["awakening"] = awakening
        _set_task(task_id, update_dict)

    except Exception as exc:
        import traceback
        print(f"[distill] Background task {task_id} failed: {exc}\n{traceback.format_exc()}")
        readable = str(exc).split("\n")[0].strip() or type(exc).__name__
        _set_task(task_id, {"status": "error", "message": f"蒸馏失败：{readable}", "text_id": text_id, "character": char_name})
        # Clean up half-done cards (empty card_json)
        try:
            async def _cleanup():
                store = get_storage()
                await store.cleanup_empty_cards(text_id, user_id)
            run_on_main_loop(_cleanup())
        except Exception as cleanup_err:
            print(f"[distill] Cleanup half-done cards failed (non-fatal): {cleanup_err}")
    finally:
        # 终态确认在 release 之前：若刚才的终态 _set_task 落库失败，这里补最后一次
        # 写，否则 DB 行会永久停 running 占 count_running 槽（release 了 DB 没跟上）。
        _confirm_terminal_persist(task_id)
        _DISTILL_SEMAPHORE.release()
        # 按用户槽同样最后放：先让 DB 终态落定，再允许该用户开下一个任务。
        _release_user_slot(user_id)


# ---- Shared helpers ----

async def _do_identify(text: str, distiller: Distiller) -> dict[str, Any]:
    """Core identify logic shared by new and legacy routes."""
    if not text.strip():
        raise HTTPException(400, "Text cannot be empty")
    try:
        chars = await asyncio.to_thread(distiller.identify_characters, text)
    except Exception as exc:
        print(f"[distill] Identify characters failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    return {"characters": chars}


async def _resolve_character_name(
    text: str, character_name: str, distiller: Distiller
) -> str:
    """Auto-identify the first character if no name was provided."""
    name = character_name.strip()
    if name:
        return name
    try:
        chars = await asyncio.to_thread(distiller.identify_characters, text)
    except Exception as exc:
        print(f"[distill] Auto-identify failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
    if not chars:
        raise HTTPException(400, "No characters identified")
    name = chars[0].get("name", "")
    if not name:
        raise HTTPException(400, "Identified result missing name")
    return name


# ---- New routes (storage-backed, via TextManager) ----

@router.post("/identify")
async def identify_by_text_id(
    req: IdentifyByIdRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Identify characters from a text stored in the database."""
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    cached = await storage.get_characters(req.text_id)
    if cached:
        return {"characters": cached}
    text_rec = await storage.get_text_owned(req.text_id, user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    result = await _do_identify(text_rec["content"], distiller)
    await storage.save_characters(req.text_id, result["characters"])
    return result


@router.post("/run")
async def distill_by_text_id(
    req: DistillByIdRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Distill a character from a stored text, persist card + session."""
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text_owned(req.text_id, user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    # Fetch user's embedding key so RAGEngine can initialize DashScope embedding
    _api_config = await storage.get_user_api_config(user_id)
    _ek = (_api_config or {}).get("embedding_key", "")
    _er = (_api_config or {}).get("embedding_region", "")

    content = _get_distill_content(text_rec)
    char_name = await _resolve_character_name(content, req.character_name, distiller)

    try:
        result = await text_manager.get_or_distill(
            req.text_id, char_name, force=req.force, user_id=user_id,
            embedding_key=_ek, embedding_region=_er,
        )
        # Fire-and-forget scene index via isolated service
        indexing_service = get_indexing_service()
        if indexing_service:
            indexing_service.schedule_scene_index(
                req.text_id, result.get("card_id", ""), content, char_name,
                all_characters=[], embedding_key=_ek, embedding_region=_er,
            )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.post("/start")
async def distill_start(
    req: DistillTaskRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """按用户并发闸包住 _distill_start_impl：占坑 → 两道门 → 跑。

    槽位所有权只在 impl **正常返回**时转移给后台线程（由 _run_distill_task 退出时
    释放）。impl 抛任何异常（404 / 503 / DB 出错）都在这里释放，否则该用户会被自己
    永久挡住。占坑是第一条语句，前面没有任何 await。
    """
    user_id = user["id"]
    if not _reserve_user_slot(user_id):
        raise HTTPException(429, _DISTILL_BUSY_MSG)
    try:
        # 第二道门：DB 真相复核。占坑挡进程内并发，这道挡跨重启残留的 running 行；
        # 时效窗让超期未刷新的幽灵行出局（见 DISTILL_GHOST_IDLE_MIN）。
        occupied = await storage.count_running_distills(
            user_id, window_minutes=DISTILL_GHOST_IDLE_MIN)
        if occupied >= DISTILL_MAX_PER_USER:
            raise HTTPException(429, _DISTILL_BUSY_MSG)
        return await _distill_start_impl(req, request, user, storage)
    except BaseException:
        _release_user_slot(user_id)
        raise


async def _distill_start_impl(
    req: DistillTaskRequest,
    request: Request,
    user: dict,
    storage: StorageBase,
) -> dict[str, Any]:
    """Start distillation as a background task, return task_id immediately."""
    from deps import get_distiller
    user_id = user["id"]
    _client_ip = get_client_ip(request)

    # Resolve per-user LLM config for the background thread
    api_config = await storage.get_user_api_config(user_id)
    per_user_llm = None
    if api_config and api_config.get("api_key"):
        from web.geo_guard import check_api_allowed
        base_url = api_config.get("base_url", "https://api.deepseek.com")
        allowed, reason = check_api_allowed(_client_ip, base_url)
        if not allowed:
            await storage.record_geo_block(user_id, _client_ip, base_url, reason)
        else:
            from adapters.llm_adapter import LLMAdapter
            try:
                per_user_llm = LLMAdapter(
                    api_key=api_config["api_key"],
                    base_url=base_url,
                    model=api_config.get("model", "deepseek-v4-pro"),
                )
            except Exception:
                pass

    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    # Read text content in the async endpoint so the background thread
    # doesn't need to call asyncio storage methods (cross-thread safe).
    text_rec = await storage.get_text_owned(req.text_id, user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    text_type = text_rec.get("text_type", "story")
    content = _get_distill_content(text_rec)
    text_fp = text_fingerprint(content)
    chunk_size = distiller.effective_chunk_size(text_type)

    # ── 续跑发现：复用上个进程遗留的 interrupted 任务 ────────────────────
    # boot reconcile 把孤儿 running 置 interrupted（web/server.py）。同一 (user,
    # text, character) 有 interrupted 行就复用它：前端继续轮询同一 task_id、不产生
    # 重复行，分片候选按该行 checkpoint 取。force=True 是「重新蒸馏」，不复用。
    # 精确匹配 character：同一文本下两个角色绝不能互借缓存片。
    resume_candidates: dict[int, dict] | None = None
    existing = None
    if not req.force:
        try:
            existing = await storage.find_interrupted_distill(user_id, req.text_id, req.character_name)
        except Exception as exc:
            # 发现失败当新任务：宁可整跑，不因发现环节拒启动
            print(f"[distill] Resume discovery failed (non-fatal, start fresh): {exc}")

    if existing is not None:
        task_id = existing["task_id"]
        # 任务级门（先于分片三重门）：切分参数 / 全文指纹任一不符 → 整批作废。
        # 两种故障分开记日志：用户看到的解释不同（换了解析参数 vs 换了原文）。
        if existing.get("chunk_size") != chunk_size:
            print(f"[distill] Resume {task_id} rejected: 切分参数变更 "
                  f"chunk_size {existing.get('chunk_size')} → {chunk_size}，整批重跑")
        elif existing.get("text_fingerprint") != text_fp:
            print(f"[distill] Resume {task_id} rejected: 原文变更（text_fingerprint 不符），整批重跑")
        else:
            rows = await storage.get_distill_chunks(task_id)
            resume_candidates = {
                r["chunk_index"]: {"result": r["result"], "fingerprint": r["chunk_fingerprint"]}
                for r in rows
            }
            print(f"[distill] Resume {task_id}: {len(resume_candidates)} 片候选，任务级门通过")
    else:
        task_id = _uuid.uuid4().hex[:12]

    # DB 先落一行（queued 粗粒度记 running），再放内存写缓存、再启线程。三者都发生在
    # loop 线程：DB 写在 thread.start() 前完成，bg 线程的 _set_task 只做 update（不再 upsert）。
    # 落库失败必须拒绝启动：DB 是查询真相源，无行 = 不可观测的野线程 —— 查询会 404
    # 报"任务丢失"，后台却仍在烧 LLM 额度、占 semaphore。绝不能"能跑但不给查"。
    # chunk_size/text_fingerprint 每次起跑都盖章：复用行重跑时也要刷新成当前 checkpoint
    # （否则陈旧值会让下次续跑反复误判）。overlap 无对应切分概念（_split_chunks 无重叠），
    # 保持 NULL。
    _queued_msg = f"排队中(最多同时{DISTILL_MAX_CONCURRENT}个蒸馏)"
    try:
        if existing is not None:
            # 复用行：盖章 + 置 running 走 UPDATE（绝不 INSERT）。返回 0 = 行在发现与盖章
            # 之间被删（如文本被删）→ 拒绝启动，否则会起一条查询 404 的野线程。一次原子
            # UPDATE 的返回即裁决，没有 SELECT-then-UPDATE 的窗口。
            affected = await storage.update_distill_task(
                task_id, status="running", progress_pct=0, message=_queued_msg,
                card_id="", awakening="",
                chunk_size=chunk_size, text_fingerprint=text_fp,
            )
            if affected == 0:
                print(f"[distill] Resume row {task_id} gone before restamp; refusing to start")
                raise HTTPException(503, "蒸馏任务创建失败，请稍后重试")
        else:
            # 新任务：纯 INSERT。主键冲突是真异常（新铸 uuid），由下面 except 兜成 503。
            await storage.create_distill_task(
                task_id, user_id, req.text_id, req.character_name,
                status="running", progress_pct=0, message=_queued_msg,
                card_id="", awakening="",
                chunk_size=chunk_size, text_fingerprint=text_fp,
            )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[distill] Create distill task row failed; refusing to start: {exc}")
        raise HTTPException(503, "蒸馏任务创建失败，请稍后重试") from exc

    with _task_lock:
        _tasks[task_id] = {"status": "queued", "progress_pct": 0, "user_id": user_id,
                           "text_id": req.text_id, "character": req.character_name,
                           "message": _queued_msg,
                           "card_id": "", "awakening": "", "_db": ("running", 0)}

    thread = T.ctx_thread(  # OTel context 传播点：蒸馏后台线程挂到发起请求 trace
        _run_distill_task,
        args=(task_id, req.text_id, req.character_name, req.force, user_id, content, text_type,
              api_config, _client_ip, resume_candidates),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


# ── 任务状态契约（服务端唯一真相）───────────────────────────────────────────
#   done          唯一终态判据。前端据它停止轮询。对齐 AIP-151 长任务的 done 布尔。
#   poll_after_ms 非终态的轮询节奏，由服务端决定；终态为 0。对应 Azure LRO 的
#                 Retry-After / MCP Tasks 的 pollInterval。
#   actions       可执行动作 token，前端只渲染此处出现的。**本项目自有扩展，非任何
#                 标准** —— 标准里没有"可续跑的中断态"这个概念。resume 与 retry 都
#                 打到 POST /api/distill/start：续跑 vs 整批重跑由后端任务级门
#                 （chunk_size / text_fingerprint）自行裁决，前端不判断。二者只差文案。
_DEFAULT_POLL_MS = 3000


def _task_affordances(status: str) -> tuple[bool, list[str], int]:
    """(done, actions, poll_after_ms) —— 决定这三者的唯一地方，全仓不得有第二处计算。

    未知 status 按**非终态**处理：宁可多轮询一次，不可让前端以为任务已终结、
    停止观察一个可能仍在跑的任务。
    """
    return {
        "running":     (False, ["cancel"], _DEFAULT_POLL_MS),
        "done":        (True,  [],         0),
        "error":       (True,  ["retry"],  0),
        "interrupted": (True,  ["resume"], 0),
    }.get(status, (False, [], _DEFAULT_POLL_MS))


def _task_response(row: dict[str, Any], mem: dict[str, Any] | None = None) -> dict[str, Any]:
    """任务状态对象序列化器 —— distill 域内唯一出口，新增字段一律加在这里。

    **域边界**：只收口蒸馏任务（distill_tasks 表）。上传预处理任务是独立域
    （routers/text.py 的 _upload_tasks：纯内存、不落库、无 interrupted 态、无续跑，
    前端走 uploadTaskProgress 独立消费）——它**不共用**此契约，不是遗漏，别来"顺手统一"。

    status/progress_pct/所有权/存在性永远以 DB 为准；mem 只作展示字段细化，且仅在
    DB running 时覆盖 stage/message（跨重启后 mem 为空 → stage 空串、message 回落
    DB 值，展示不崩、四态不谎）。
    """
    status = row["status"]
    done, actions, poll_after_ms = _task_affordances(status)
    resp = {
        "task_id": row["task_id"],
        "status": status,
        "done": done,
        "actions": actions,
        "poll_after_ms": poll_after_ms,
        "progress_pct": row["progress_pct"],
        "message": row["message"],
        "character": row["character"],
        "text_id": row["text_id"],
        "card_id": row["card_id"],
        "awakening": row["awakening"],
        "stage": "",
    }
    if status == "running" and mem is not None:
        resp["stage"] = mem.get("status", "")
        resp["message"] = mem.get("message", resp["message"])
    return resp


@router.get("/task/{task_id}")
async def distill_task_status(
    task_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Poll distillation task status — DB 是真相源，内存只作写缓存，不作查询依据。

    DB 只有 running/done/error/interrupted 四态 + 进度，rich 阶段细节只存内存作
    **展示字段**：仅当 DB running 且本进程确有活跃条目时，用内存的 message / stage
    细化展示；status/progress_pct/所有权/存在性永远以 DB 为准，绝不被内存覆盖。
    跨重启后内存空 → stage 为空串、message 回落 DB 值，展示不崩、四态不谎。
    """
    row = await storage.get_distill_task(task_id)
    if row is None:
        raise HTTPException(404, "Task not found")
    if row.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此任务")
    # 只读覆盖展示字段：需同时满足 DB running + 本进程内存有该活跃条目。锁内浅拷一份，
    # 锁外只读 —— 序列化器不持锁，避免把 _task_lock 扩散进纯函数。
    mem = None
    if row["status"] == "running":
        with _task_lock:
            live = _tasks.get(task_id)
            if live is not None:
                mem = dict(live)
    return _task_response(row, mem)


@router.delete("/task/{task_id}")
async def cancel_distill_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, bool]:
    """Cancel a running distillation task.

    所有权以 DB 行为准（跨重启后内存可能为空）。内存更新只作本进程 bg 线程的
    停止信号（stream 循环逐 piece 查 status==error）；DB 置 error 是查询真相。
    bg 线程收到信号后走 abort 路径，其 _set_task(terminal) 会把 DB 终态补写回来，
    盖掉 cancel 与 bg 之间可能交错的最后一次 running 进度写。
    """
    row = await storage.get_distill_task(task_id)
    if row is None:
        raise HTTPException(404, "Task not found")
    if row.get("user_id") != user["id"]:
        raise HTTPException(403, "无权操作此任务")
    with _task_lock:
        task = _tasks.get(task_id)
        if task is not None and task.get("status") not in ("done", "error"):
            task.update({"status": "error", "message": "已取消"})
    try:
        await storage.update_distill_task(task_id, status="error", message="已取消")
    except Exception as exc:
        # DB 写失败仍有内存停止信号，bg 线程 abort 时还会再补一次终态落库
        print(f"[distill] Cancel task {task_id} DB update failed (non-fatal): {exc}")
    return {"ok": True}


@router.get("/task/{task_id}/params")
async def distill_task_params(
    task_id: str,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Get stored task params for retry recovery — 与 status 同源，读 DB。

    DB 是真相源：内存终态 pop 之后或跨重启后仍能取到 text_id/character，否则
    「任务成功完成」与「任务不存在」在该接口上不可分。不留内存回退分支。
    """
    row = await storage.get_distill_task(task_id)
    if row is None:
        raise HTTPException(404, "Task not found")
    if row.get("user_id") != user["id"]:
        raise HTTPException(403, "无权访问此任务")
    return {
        "task_id": task_id,
        "text_id": row["text_id"],
        "character": row["character"],
    }


def _next_piece(stream_obj):
    """Read next token from a generator; returns (token, done). Thread-safe."""
    try:
        return next(stream_obj), False
    except StopIteration:
        return "", True


@router.post("/run_stream")
@limiter.limit("16/hour")
async def distill_stream(
    req: DistillByIdRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
):
    """Stream distillation via SSE — no timeout, frontend renders tokens in real-time."""
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if text_manager is None or distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    # Fetch user's embedding key so RAGEngine can initialize DashScope embedding
    _api_config = await storage.get_user_api_config(user_id)
    _ek = (_api_config or {}).get("embedding_key", "")
    _er = (_api_config or {}).get("embedding_region", "")

    text_rec = await storage.get_text_owned(req.text_id, user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")

    content = _get_distill_content(text_rec)
    text_type = text_rec.get("text_type", "story")
    char_name = req.character_name.strip()

    distiller._storage = storage
    distiller._user_id = user_id

    async def _event_gen():
        yield f"data: {json.dumps({'status': 'identifying'}, ensure_ascii=False, default=str)}\n\n"

        # ONE LLM call: resolve name (if needed) + aliases
        nonlocal char_name
        aliases: list[str] = []
        try:
            chars = await asyncio.to_thread(distiller.identify_characters, content)
        except Exception as exc:
            print(f"[distill] Identify failed: {exc}")
            chars = []
        if not char_name:
            if not chars:
                yield f"data: {json.dumps({'error': '未识别到任何角色'}, ensure_ascii=False, default=str)}\n\n"
                return
            char_name = chars[0].get("name", "")
            if not char_name:
                yield f"data: {json.dumps({'error': '识别结果缺少角色名'}, ensure_ascii=False, default=str)}\n\n"
                return
        for c in chars:
            if c.get("name") == char_name:
                aliases = c.get("aliases", [])
                break

        # Incremental distillation with aliases for broader chunk matching
        full = ""
        stream = distiller.distill_incremental_stream(content, char_name, aliases, text_type)
        while True:
            try:
                piece, done = await asyncio.to_thread(_next_piece, stream)
            except Exception as exc:
                print(f"[distill] Stream failed: {exc}")
                yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False, default=str)}\n\n"
                return
            if done:
                break
            if isinstance(piece, dict) and piece.get("heartbeat"):
                yield f"data: {json.dumps({'heartbeat': True}, default=str)}\n\n"
                continue
            if isinstance(piece, dict):
                # Progress event from incremental chunk processing
                yield f"data: {json.dumps(piece, ensure_ascii=False, default=str)}\n\n"
            else:
                full += piece
                yield f"data: {json.dumps({'token': piece}, ensure_ascii=False, default=str)}\n\n"

        # Step 3: Parse + validate + save
        stripped = full.strip()
        data = None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(stripped[start:end + 1])
                except json.JSONDecodeError:
                    pass

        if data is None:
            yield f"data: {json.dumps({'error': '蒸馏失败：LLM 返回格式不正确'}, ensure_ascii=False, default=str)}\n\n"
            return

        try:
            card = CharacterCard.model_validate(data)
        except Exception as exc:
            print(f"[distill] Card validation failed: {exc}")
            yield f"data: {json.dumps({'error': f'蒸馏失败：数据校验错误 {exc}'}, ensure_ascii=False, default=str)}\n\n"
            return

        # Persist card + create session (RAG built in _create_session for chat use)
        try:
            result = await text_manager.save_distilled_card(
                req.text_id, card, user_id,
                embedding_key=_ek, embedding_region=_er,
            )
        except Exception as exc:
            print(f"[distill] Save card failed: {exc}")
            yield f"data: {json.dumps({'error': f'保存角色卡失败：{exc}'}, ensure_ascii=False, default=str)}\n\n"
            return

        # Generate awakening line (fails open, async context)
        awakening = ""
        if per_user_llm is not None:
            awakening = await asyncio.to_thread(_generate_awakening, per_user_llm, card)

        # Persist awakening_message to card (non-fatal)
        if awakening:
            try:
                card.awakening_message = awakening
                await get_storage().update_card(result.get("card_id", ""), card.model_dump())
                print(f"[distill] Persisted awakening_message to card {result.get('card_id', '')}")
            except Exception as exc:
                print(f"[distill] Persist awakening_message to card failed (non-fatal): {exc}")

        # 注意：这里的 done 是 SSE **流结束标记**，与任务契约里 _task_affordances 的
        # done（任务终态判据）只是撞名。本流不建 DB 任务行、不产任务状态对象，故不带
        # status/actions/poll_after_ms —— 前端 normalizeTask 的 task_id+status 门会挡掉
        # 它，不会被误判成任务终态。改这里前先看前端那道门。
        done_payload = {'done': True, 'awakening': awakening, **result}
        yield f"data: {json.dumps(done_payload, ensure_ascii=False, default=str)}\n\n"

    # OTel context 传播点：distill 根 span（wf=distill 与 chat 分链路统计）
    return StreamingResponse(
        T.trace_sse_async(_event_gen(), "distill.stream", wf="distill"),
        media_type="text/event-stream",
    )


@router.post("/reindex/{text_id}")
async def reindex_rag(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Rebuild RAG indices for all in-memory sessions with character metadata.

    Reads the text from storage, runs identify_characters, then rebuilds
    each session's RAG index to include character tags so that
    ``character_name`` filtering works in subsequent chat queries.
    """
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    text_rec = await storage.get_text_owned(text_id, user_id)
    if not text_rec:
        raise HTTPException(404, "Text not found")
    content = _get_distill_content(text_rec)

    try:
        chars = await asyncio.to_thread(distiller.identify_characters, content)
    except Exception as exc:
        print(f"[distill] Reindex identify failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    count = 0
    for sid, session in sessions.items():
        engine = session.get("engine")
        if engine is None:
            continue
        try:
            engine.rag.index(content, all_characters=chars)
            engine._all_characters = chars
            count += 1
        except Exception as exc:
            print(f"[distill] Reindex session {sid} failed: {exc}")

    return {"reindexed_sessions": count, "characters_found": len(chars)}


class UpdateCardRequest(BaseModel):
    card_json: dict

@router.patch("/card/{card_id}")
async def update_card(
    card_id: str,
    req: UpdateCardRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
):
    record = await storage.get_card(card_id)
    if not record:
        raise HTTPException(404, "Card not found")
    if record.get("user_id") != user["id"]:
        raise HTTPException(403, "无权修改此角色卡")
    # Schema gate: raw PATCH had no filter — a card editor payload that is not a
    # structurally valid CharacterCard is rejected instead of blindly persisted.
    # update_card replaces card_json wholesale, so we store the canonical dump
    # (validated) rather than the raw request dict.
    try:
        validated = CharacterCard.model_validate(req.card_json)
    except Exception as exc:
        raise HTTPException(400, f"角色卡数据校验失败：{exc}") from exc
    result = await storage.update_card(card_id, validated.model_dump())
    return {"ok": True, "card": result}


@router.get("/cards/by-text/{text_id}")
async def list_cards(
    text_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List all distilled character cards for a text."""
    user_id = user["id"]
    try:
        result = await storage.list_cards(text_id, user_id)
        print(f"[distill] list_cards text_id={text_id} user_id={user_id} => {len(result)} cards")
        return result
    except Exception as exc:
        print(f"[distill] List cards failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.get("/cards/standalone")
async def list_standalone_cards(
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> list[dict[str, Any]]:
    """List standalone cards (forked from market, no text attachment)."""
    try:
        return await storage.list_standalone_cards(user["id"])
    except Exception as exc:
        print(f"[distill] List standalone cards failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc


@router.get("/cards/{card_id}/export")
async def export_card(
    card_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    format: str = Query(default="tavern"),
    first_mes: str = Query(default=""),
) -> Response:
    """Export a character card in the requested format.

    ``format=tavern`` returns SillyTavern character-card-v2 JSON
    with ``Content-Disposition: attachment`` for direct download.
    """
    record = await storage.get_card(card_id)
    if not record:
        raise HTTPException(404, "Card not found")
    if record.get("user_id") != user["id"]:
        raise HTTPException(403, "无权导出此角色卡")

    try:
        card = CharacterCard.model_validate_json(record["card_json"])
    except Exception as exc:
        print(f"[distill] Parse card {card_id} failed: {exc}")
        raise HTTPException(500, "Card data is corrupted") from exc

    if format == "tavern":
        body = export_tavern_json(card, first_mes)
        safe_name = quote(f"{card.name}_tavern.json")
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{safe_name}"
                ),
            },
        )

    if format == "raw":
        safe_name = quote(f"{card.name}.json")
        return Response(
            content=record["card_json"],
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{safe_name}"
                ),
            },
        )

    raise HTTPException(400, f"Unsupported export format: {format}")


@router.post("/start_session")
async def start_session(
    req: StartSessionRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
    sessions: dict[str, dict[str, Any]] = Depends(get_sessions),
) -> dict[str, Any]:
    """Create a chat session for an already-distilled card.

    Reads the text and card from storage, rebuilds RAG+ChatEngine,
    injects into the in-memory ``_sessions`` dict, persists the session
    record to SQLite, and returns the card data with ``session_id``.
    """
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    text_manager = get_text_manager(llm=per_user_llm)

    card_rec = await storage.get_card(req.card_id)
    if not card_rec:
        raise HTTPException(404, "Card not found")

    try:
        card = CharacterCard.model_validate_json(card_rec["card_json"])
    except Exception as exc:
        print(f"[distill] Parse card {req.card_id} failed: {exc}")
        raise HTTPException(500, "Card data is corrupted") from exc



    try:
        if req.text_id:
            text_rec = await storage.get_text_owned(req.text_id, user_id)
            if not text_rec:
                raise HTTPException(404, "Text not found")
            content = _get_distill_content(text_rec)
            existing_cards = await storage.list_cards(req.text_id, user_id)
            all_characters = await text_manager._build_all_characters(req.text_id, existing_cards)
            emb_key = ""
            emb_region = ""
            try:
                user_cfg = await storage.get_user_api_config(user_id)
                if user_cfg.get("embedding_key"):
                    emb_key = user_cfg["embedding_key"]
                    emb_region = user_cfg.get("embedding_region", "cn")
            except Exception:
                pass
            session_id = await asyncio.to_thread(
                text_manager._create_session, content, card, all_characters, None, req.card_id, user_id,
                user_role=req.user_role,
            )
            # Fire-and-forget scene index via isolated service
            indexing_service = get_indexing_service()
            if indexing_service:
                indexing_service.schedule_scene_index(
                    req.text_id, req.card_id, content, card.name, all_characters,
                    embedding_key=emb_key, embedding_region=emb_region,
                )
        else:
            # 独立卡片模式：不加载原文，不构建 RAG，直接创建 ChatEngine
            from core.chat_engine import ChatEngine
            from deps import get_rag_config, get_memory_manager
            engine = ChatEngine(
                per_user_llm, None, card,
                memory_manager=get_memory_manager(),
                card_id=req.card_id,
                user_role=req.user_role,
            )
            session_id = _uuid.uuid4().hex[:12]
            sessions[session_id] = {"engine": engine, "lock": asyncio.Lock(), "message_ids": []}
            all_characters = []
    except Exception as exc:
        print(f"[distill] Create session for card {req.card_id} failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    try:
        await storage.save_session(session_id, req.card_id, req.user_role, user.get("avatar_data", ""), user_id)
    except Exception as exc:
        print(f"[distill] Persist session failed (non-fatal): {exc}")

    # ── Inject opening line (always generate when LLM is available) ──
    opening = ""
    if per_user_llm is not None:
        try:
            style = card.speaking_style
            traits = "，".join(card.personality_traits[:3])
            seed = card.first_message or ""
            user_context = f"对「{req.user_role}」" if req.user_role else "对初次见面的陌生人"
            from core.clock import UserClock, describe_time_period
            _now = UserClock.now(req.client_tz)
            _period = describe_time_period(_now.hour)
            seed_line = f"惯常开场白参考：「{seed}」\n" if seed else ""
            prompt = (
                f"以「{card.name}」的口吻，{user_context}说此刻的第一句话。\n"
                f"身份：{card.identity}\n"
                f"性格：{traits}\n"
                f"语气：{style.tone}\n"
                f"口癖：{', '.join(style.catchphrases) if style.catchphrases else '无'}\n"
                f"{seed_line}"
                f"当前时段：{_period}（{_now.hour}点）\n\n"
                f"先用不超过15字的括号动作把自己放进当下场景，再说话。"
                f"时间藏在语气里不点明。\n"
                f"(动作)台词，台词不超过50字。"
            )
            opening = await asyncio.to_thread(
                per_user_llm.chat, prompt, [{"role": "user", "content": "请说开场白"}]
            )
            opening = opening.strip().strip('"').strip("'").strip("「」")
            if opening and len(opening) <= 100:
                print(f"[start_session] Generated opening: {opening}")
            else:
                opening = card.first_message or ""
        except Exception as exc:
            print(f"[start_session] Generate opening line failed (non-fatal): {exc}")
            opening = card.first_message or ""
    else:
        opening = card.first_message or ""

    # Save opening to DB + engine.history (seed only, no backfill to card)
    first_created_at = ""
    if opening:
        engine_obj = sessions[session_id].get("engine") if sessions.get(session_id) else None
        if engine_obj:
            try:
                rec = await storage.save_message(session_id, "char", opening, "", retracted=False)
                first_created_at = rec.get("created_at", "")
                engine_obj.history.append({"role": "assistant", "content": opening})
                sessions[session_id].setdefault("message_ids", []).append(rec["id"])
                print(f"[start_session] Injected opening into session {session_id}")
            except Exception as exc:
                print(f"[start_session] Save opening message failed (non-fatal): {exc}")

    result = card.model_dump()
    result["session_id"] = session_id
    result["card_id"] = req.card_id
    if opening:
        result["first_message"] = opening
        result["first_created_at"] = first_created_at
    return result



# ---- Legacy compat routes (/api/identify, /api/distill) ----

@legacy_router.post("/api/identify")
async def legacy_identify(
    req: IdentifyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Legacy: identify characters from raw text body."""
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    if distiller is None:
        raise HTTPException(503, "请先在设置页配置 API Key")
    return await _do_identify(req.text, distiller)


@legacy_router.post("/api/distill")
async def legacy_distill(
    req: DistillRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Legacy: distill from raw text, auto-save text + persist card."""
    user_id = user["id"]
    _client_ip = get_client_ip(request)
    from deps import get_distiller, get_text_manager, get_user_llm
    per_user_llm = await get_user_llm(user_id, storage, client_ip=_client_ip)
    distiller = get_distiller(llm=per_user_llm)
    text_manager = get_text_manager(llm=per_user_llm)
    if distiller is None or text_manager is None:
        raise HTTPException(503, "请先在设置页配置 API Key")

    # Fetch user's embedding key so RAGEngine can initialize DashScope embedding
    _api_config = await storage.get_user_api_config(user_id)
    _ek = (_api_config or {}).get("embedding_key", "")
    _er = (_api_config or {}).get("embedding_region", "")

    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")
    try:
        upload_result = await text_manager.upload_text("legacy_upload.txt", text, user_id=user_id)
        text_id = upload_result["text_id"]
    except Exception as exc:
        print(f"[distill] Auto-save text failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc

    char_name = await _resolve_character_name(text, req.character_name, distiller)

    try:
        return await text_manager.get_or_distill(
            text_id, char_name, user_id=user_id,
            embedding_key=_ek, embedding_region=_er,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"[distill] Distill failed: {exc}")
        raise HTTPException(500, "操作失败，请稍后重试") from exc
