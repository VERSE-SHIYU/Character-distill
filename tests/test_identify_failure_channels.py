# -*- coding: utf-8 -*-
"""识别失败 / 无目标角色：三个通道（bg 任务 / SSE / HTTP）各自的形态。

缺陷 86 的形态：失败被当成「空名单」。`chars = []` 有两处宽捕获，把上游故障
（网络 / DB / 额度）与「这本书真的没有具名角色」渲染成同一句话 —— 用户看到
「没识别到角色」，运维什么都收不到，两类结果不可辨。

本文件锁三件事：

  1. **bg 任务不吞识别失败** —— 识别抛异常，任务行是失败文案，**不是**「未识别到任何角色」
  2. **SSE 不吞识别失败** —— 同上，看错误帧
  3. **判据只有一处** —— 空名单时三个通道给出**同一句**「未识别到任何角色」

第 3 条的期望文案是**字面量**（不从源码 import）：文案定义在
``core/character_roster.py`` 一处（`NoTargetCharacter.user_message`），
变异把那一处改掉 → 三个通道的断言同时变红。

变异对象一览：
  - 1/2：把 `chars = []` 宽捕获加回去（bg 那条改回 `except Exception: chars = []`，
        SSE 那条在 `_event_gen` 里改回）→ 任务行 / 错误帧变回「未识别到任何角色」
  - 3：  把 `_NO_TARGET_MESSAGES` 里的中文改一个字 → 三通道断言同时红
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import deps
import server
from core.distiller import DISTILL_PROMPT_BEFORE_NAME, DistillError, Distiller
from core.schema import FORMAT_GROUPS, CharacterCard
from core.text_manager import TextManager
from deps import get_storage
from routers import distill as D
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore

# WP7 F4 复用 WP7 那批格式化件（认组别 + 按组回 JSON），不另抄一份字段样例：
# 抄一份就是第二处「组字段表」，组一变就漂。cross-module import 在本仓有先例
# （test_postgres_store 引 test_published_from_backfill）。
from test_distiller_routing import (
    _FakeAsyncClient,
    _SAMPLE_FIELD_VALUES,
    _format_group_of,
    _group_reply,
)

# 三通道共用的期望文案。改动被锁的是「定义文案的那一处」（character_roster），
# 所以这里必须是复制过来的字面量 —— 从源码 import 就自证自明，变异杀不掉。
EMPTY_ROSTER_TEXT = "未识别到任何角色"
FAILURE_TEXT = "识别失败：上游接口限流，请稍后重试"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _RaisingDistiller:
    """识别直接抛 —— 模拟上游故障（网络 / 额度 / DB）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def identify_characters(self, content):
        raise self._exc


class _EmptyRosterDistiller:
    """识别**成功**，但名单是空的 —— 这本书真的没有具名角色。"""

    def identify_characters(self, content):
        return []


class _OpenSemaphore:
    def acquire(self, timeout=0):
        return True

    def release(self):
        pass


class _TM:
    """只保证 `/run` 与 `/run_stream` 的 503 门过得去；本文件不考蒸馏结果。"""

    async def get_or_distill(self, *a, **kw):
        raise AssertionError("无目标角色时不该走到蒸馏")


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _clean_tasks():
    yield
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()


# 正文默认带角色的对话引号句：落卡前要按编号从原文挑对话示例（WP17），挑不出即任务
# 失败 —— 用「角色说的话」这种无引号正文，几条通道用例会全部红在「找不到对话句」上。
_BODY = "路人道：“先前的话。”\n角色道：“我说一句话。”"


def _seed_text(store, uid, body=_BODY):
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


def _build_app(store, uid, monkeypatch, *, distiller, tm=None):
    app = FastAPI()
    app.include_router(D.router)
    # legacy 两条（`POST /api/identify` / `POST /api/distill`）也挂上：它们的依赖是
    # 函数体里 late-import 的，上面的 monkeypatch 一样管用。
    app.include_router(D.legacy_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "is_admin": False,
    }
    app.dependency_overrides[D.get_sessions] = lambda: {}
    server.register_domain_error_handlers(app)   # 与生产装配同一处

    async def _llm(*a, **kw):
        return object()

    # 必须走 monkeypatch：裸赋值 `deps.get_user_llm = ...` 会泄漏到同进程后续用例
    # （test_llm_access_gate 的 L1–L4 就是靠 deps.get_user_llm 取实例的，被污染即红）。
    monkeypatch.setattr(deps, "get_user_llm", _llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: distiller)
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm or _TM())
    return app


def _build_client(store, uid, monkeypatch, *, distiller, tm=None):
    return TestClient(
        _build_app(store, uid, monkeypatch, distiller=distiller, tm=tm),
        raise_server_exceptions=False)


def _run_bg(store, user_id, text_id, distiller, monkeypatch, body=_BODY):
    """驱动真 bg 任务的失败收口，返回落库后的任务行。

    直接调 `_run_distill_task`（改动就在它里面），不经 HTTP：本文件不考路由与槽位，
    那两条各自的用例在 test_distill_task_api.py。`body` 是交给蒸馏的正文（默认带对话
    引号；预检那条用例要一段挑不出对话句的）。
    """
    task_id = f"dt_{uuid.uuid4().hex}"
    _run_async(store.create_distill_task(
        task_id, user_id, text_id, character="", status="running", progress_pct=0,
        message="进行中", card_id="", awakening="",
    ))
    with D._task_lock:
        D._tasks[task_id] = {
            "status": "running", "progress_pct": 0, "user_id": user_id,
            "text_id": text_id, "character": "", "message": "",
            "card_id": "", "awakening": "", "_db": None,
        }

    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "submit_to_main_loop", lambda coro, timeout=10: asyncio.run(coro))
    monkeypatch.setattr(D, "_DISTILL_SEMAPHORE", _OpenSemaphore())
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: distiller)

    D._run_distill_task(
        task_id, text_id, "", False, user_id, body, "story", object(),
    )
    return _run_async(store.get_distill_task_unscoped(task_id))


def _sse_errors(text: str) -> list[dict]:
    frames = [json.loads(l[len("data: "):]) for l in text.splitlines() if l.startswith("data: ")]
    return [f for f in frames if "error" in f]


# ── 1. bg 任务不吞识别失败 ────────────────────────────────────────────────


class TestBgTaskDoesNotSwallowIdentifyFailure:
    def test_identify_failure_is_the_task_message(self, store, user_id, monkeypatch):
        """识别抛异常 → 任务行是那条失败文案，不是「未识别到任何角色」。

        变异对象 = 把宽捕获加回 bg：`except Exception: chars = []` → 空名单 →
        `target_character_name` 抛 NoTargetCharacter → 任务行变成「未识别到任何角色」，本用例红。
        """
        tid = _seed_text(store, user_id)
        distiller = _RaisingDistiller(DistillError(FAILURE_TEXT, "API 429；9/12 个分片失败"))

        row = _run_bg(store, user_id, tid, distiller, monkeypatch)

        assert row["status"] == "error"
        assert row["message"] == FAILURE_TEXT
        assert row["message"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"
        assert "429" not in row["message"], "运维口径漏上屏"


# ── 2. SSE 不吞识别失败 ──────────────────────────────────────────────────


class TestStreamDoesNotSwallowIdentifyFailure:
    def test_identify_failure_is_the_error_frame(self, store, user_id, monkeypatch):
        """识别抛异常 → SSE 错误帧是那条失败文案，不是「未识别到任何角色」。

        变异对象 = 把宽捕获加回 `_event_gen`：帧里变成「未识别到任何角色」，本用例红。
        """
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch,
            distiller=_RaisingDistiller(DistillError(FAILURE_TEXT, "API 429")),
        )

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        assert r.status_code == 200
        errs = _sse_errors(r.text)
        assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
        assert errs[0]["error"] == FAILURE_TEXT
        assert errs[0]["error"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"


# ── 3. 判据只有一处：三通道同一句话 ───────────────────────────────────────


class TestNoTargetCharacterHasASingleSource:
    """空名单时三通道给出同一句「未识别到任何角色」。

    三份判据（HTTP 的 `_first_character_name`、bg 与 SSE 各自手抄的两段）原先各写
    一遍文案。判据下沉到 `core/character_roster.target_character_name` 后，文案只
    定义在 `NoTargetCharacter.user_message` 一处 —— 本组三个通道断言同一句字面量。

    变异对象 = 改 `_NO_TARGET_MESSAGES` 里的中文：三处断言**同时**变红。
    """

    def test_bg_task_says_it(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)

        row = _run_bg(store, user_id, tid, _EmptyRosterDistiller(), monkeypatch)

        assert row["status"] == "error"
        assert row["message"] == EMPTY_ROSTER_TEXT

    def test_sse_frame_says_it(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch, distiller=_EmptyRosterDistiller())

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        errs = _sse_errors(r.text)
        assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
        assert errs[0]["error"] == EMPTY_ROSTER_TEXT

    def test_http_says_it(self, store, user_id, monkeypatch):
        """HTTP：不点名时不走识别失败，而是「挑不出角色」→ 400 + 同一句文案。

        码 400 由 `web/server.py::_domain_error_status` 按 MRO 给（NoTargetCharacter
        是 DistillError 的子类），路由不再自己配码。
        """
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch, distiller=_EmptyRosterDistiller())

        r = client.post("/api/distill/run", json={"text_id": tid})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == EMPTY_ROSTER_TEXT


# ── 4. 识别族 HTTP 路由不吞 DistillError（同一缺陷的 HTTP 侧收尾） ────────

# `_identify_single_call` 两次解析均失败时抛的那个 DistillError 的上屏文案。
# 同上面的口径：复制字面量，不从源码 import（import 就自证自明，变异杀不掉）。
PARSE_FAIL_TEXT = "识别失败：模型返回的角色名单无法解析，请重试"


class _NonJSONLLM:
    """两次都回非 JSON —— `_identify_single_call` 的首次与重试解析都会失败。

    刻意用**真 `Distiller`** 而不是桩：本组判的正是「识别层抛出的 `DistillError`
    能不能穿过路由层到达统一出口」，桩掉识别层就把被测的那一段换掉了。
    """

    model = "fake-model-identify-parse-fail"

    def __init__(self) -> None:
        self.last_usage = None
        self.calls = 0

    def chat(self, system, messages):
        self.calls += 1
        return "这不是一个 JSON 数组"


class _StubTextManager:
    """只让 legacy `/api/distill` 走过 `upload_text` 那一跳 —— 本文件不考上传。"""

    async def upload_text(self, filename, content, **kw):
        return {"text_id": f"txt_{uuid.uuid4().hex}"}


def _failing_distiller() -> Distiller:
    return Distiller(llm=_NonJSONLLM(), config_path=None)


class TestIdentifyFamilyRoutesKeepDistillError:
    """三条识别族 HTTP 路由不再就地包 `HTTPException(500, "操作失败，请稍后重试")`。

    原先那条宽捕获拦下 `DistillError`：单分片解析失败从 400 变 500，用户也看不到
    「名单无法解析」这个真实原因。删掉捕获后，异常冒泡到 `web/server.py` 的
    `_domain_error_handler`（400 + `user_message`）—— 同文件的 `/identify`（带
    text_id）本来就是这个形状。

    变异对象 = 在任一处加回 `except Exception: raise HTTPException(500, ...)`
    → 该用例状态码 500、detail 变「操作失败，请稍后重试」，红。
    """

    def test_legacy_identify_parse_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/identify`（`_do_identify`）→ 400 + 真实原因。"""
        client = _build_client(
            store, user_id, monkeypatch, distiller=_failing_distiller())

        r = client.post("/api/identify", json={"text": f"甲说了一句话。{uuid.uuid4().hex}"})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT

    def test_legacy_distill_auto_identify_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/distill` 不点名 → `_resolve_character_name` → 400 + 真实原因。"""
        client = _build_client(
            store, user_id, monkeypatch,
            distiller=_failing_distiller(), tm=_StubTextManager())

        r = client.post(
            "/api/distill",
            json={"text": f"甲说了一句话。{uuid.uuid4().hex}", "character_name": ""})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT

    def test_reindex_identify_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/distill/reindex/{id}` 的名单段 → 400 + 真实原因。"""
        tid = _seed_text(store, user_id, body=f"角色说的话{uuid.uuid4().hex}")
        client = _build_client(
            store, user_id, monkeypatch, distiller=_failing_distiller())

        r = client.post(f"/api/distill/reindex/{tid}")

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT


# ── 5. 点名 `/run` 的别名解析不吞 DistillError（TextManager 那处宽捕获） ───


class TestNamedRunKeepsIdentifyFailure:
    """点名蒸馏走 `TextManager.get_or_distill`：卡片未命中时要先取别名。

    那段取别名原先自带 `except Exception: print(...)`，识别失败被**降级成「没有别名」**
    —— 用户看到蒸馏照常成功、只是别名缺失，故障无声。删掉宽捕获后 `DistillError` 冒泡到
    路由的 `except DistillError: raise`（`/run` 与 legacy `/api/distill` 各一处），
    由 `web/server.py` 统一出口给出 400 + `user_message`。

    变异对象 = 把宽捕获加回 `core/text_manager.py` 的别名段（`except Exception: print(...)`）
    → 识别失败被吞，代码继续往下走（桩上没有 `distill_incremental` → AttributeError）→
    500「操作失败，请稍后重试」→ 本用例的 400 断言红。
    """

    def test_named_run_identify_failure_is_400(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller = _RaisingDistiller(DistillError(FAILURE_TEXT, "API 429"))
        tm = TextManager(lambda: store, distiller, object(), {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "甲"})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == FAILURE_TEXT
        assert r.json()["detail"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"


class _RosterDistiller:
    """点名蒸馏的最小桩：识别给一份名单、格式化回一张卡、后置落卡。

    `finalize_card` 贴的与 `distill_incremental` 写进卡里的**不同** —— 这样「示例最终
    落在成品里」才不是自证的（漏接时成品留着前者）。
    """

    def identify_characters(self, content):
        return [{"name": "角色"}]

    def dialogue_candidates(self, content, name, aliases=(), roster=()):
        # 预检（补充 1-第 4 步）：通道拿到别名之后先调它一眼，返回值不保留。本组不考
        # 抽取，给个非空即可 —— 抽取与两条失败判据在 test_distiller_dialogue_pick 里核。
        return [object()]

    def distill_incremental(self, content, character_name, aliases=None, **kw):
        return CharacterCard.model_validate(
            {"name": character_name, "dialogue_examples": ["模型编的示例"]})

    def finalize_card(self, card, content, name, aliases=(), roster=()):
        card_dict = card.model_dump()
        card_dict["dialogue_examples"] = ["路人：先前的话。\n角色：我说一句话。"]
        return CharacterCard.model_validate(card_dict)


class TestRunAttachesDialogueExamples:
    """点名 `/run` 走 `TextManager.get_or_distill` —— 这条通道也要贴对话示例（WP17）。

    三条产卡通道（bg 任务 / SSE 流 / TextManager）共用
    `Distiller.attach_dialogue_examples`，各接一次；漏接的那条静默落一张空示例的卡，
    成品上与「本来就没有」分不出来。

    变异：删掉 `core/text_manager.py` 里那一跳 → 成品留着模型编的示例，断言红。
    """

    def test_the_card_from_run_carries_the_examples(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller = _RosterDistiller()
        tm = TextManager(lambda: store, distiller, None, {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "角色"})

        assert r.status_code == 200, r.text
        assert r.json()["dialogue_examples"] == ["路人：先前的话。\n角色：我说一句话。"]


# ── 6. WP7：4 组并行的产物在两条消费路径上都是「一张能解析的卡」 ──────────


class _FormattingLLM:
    """真 `Distiller` 的桩 LLM —— 识别 / 分片 / 归并 / 4 组格式化各回各的。

    用**真 `Distiller`** 而不是桩：本组判的正是「4 组的产物怎么被交出去」，桩掉蒸馏器
    就把被测的那一段换掉了（同上面 `_NonJSONLLM` 的理由）。
    """

    model = "fake-model-wp7-format"
    last_usage = None

    def __init__(self) -> None:
        self.format_groups: list[str] = []

    def _make_async_client(self):
        return _FakeAsyncClient()            # Map 阶段：建 client → 跑 → 关

    def chat(self, system, messages, max_tokens=None, **kw):
        """按提示词分三路回：合并稿、整卡格式化、识别名单（与 `_auto_tag`）。

        识别那一跳的名单里必须有上一句的说话人「路人」：挑选对话示例的 enum 只由名单里
        的**其他人**组成，没有它就没有可选的对方，成不了组（补充1-第2步）。

        `TextManager` 那条通道走**非流式**的 `distill_incremental`：合并（`_single_reduce`）
        与建卡都落在 `chat` 上，且建卡是**一次回整张卡**（不分组）。整卡 JSON 由各组样例
        合并而成（`FORMAT_GROUPS` 是卡字段的一个划分），与流式那条覆盖同一批字段。
        """
        if "你正在整合关于" in system:
            return "合并档案"
        if DISTILL_PROMPT_BEFORE_NAME in system:
            merged: dict = {}
            for g in FORMAT_GROUPS:
                merged.update(json.loads(_group_reply(g)))
            return json.dumps(merged)
        return json.dumps([{"name": "角色"}, {"name": "路人"}])

    async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
        return ("片段分析", {"prompt_tokens": 1, "completion_tokens": 1})

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
        if "你正在整合关于" in system:
            yield "合并档案"
            return {"prompt_tokens": 1, "completion_tokens": 1}
        group = _format_group_of(system)
        self.format_groups.append(group)
        yield _group_reply(group)
        return {"prompt_tokens": 1, "completion_tokens": 1}

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        """挑选对话示例：只回编号与上一句说话人（文字由代码从原文复制）。编号只发给说话人
        是「角色」的候选，正文里那条就是 1 号；上一句说话人从名单 enum 里取「路人」——
        让这一步真的跑通，别把它桩掉（本组考的是 4 组怎么并成一张卡）。"""
        return {"picks": [{"n": 1, "prev_speaker": "路人"}]}


def _card_distiller() -> Distiller:
    d = Distiller(llm=_FormattingLLM(), config_path=None)
    d._longctx_threshold = 0        # 本组要的是 Format 阶段，短正文也走分片那条
    d._chunk_size = 3000
    return d


class _SavingTM:
    """只让收尾那跳过得去（`save_distilled_card`）；本组不考落库与会话。"""

    def __init__(self) -> None:
        self.saved: list[CharacterCard] = []

    async def save_distilled_card(self, text_id, card, user_id, **kw):
        self.saved.append(card)
        return {"card_id": f"card_{uuid.uuid4().hex[:8]}"}


class TestOneParseableCardOnBothChannels:
    """WP7 F4：4 组并行后**恰一个** str 帧，两条消费路径都从累加串里 parse 出卡。

    两条路径收非 dict 帧的写法不同，但都假定「所有 str 帧拼起来正好是一个 JSON」：
    `_run_distill_task` 先 `json.loads(整串)` 再退到「首个顶层 `{}`」，`_event_gen`
    退到「首个 `{` 到末个 `}`」。4 组各 yield 一次的话（本组变异）两种退路都拼不出
    一张完整的卡 —— 前者只会捞到 G1 那一组，后者连 JSON 都不是。

    桩 LLM 按组回 JSON，认组复用 WP7 那批件（`_format_group_of` / `_group_reply`），
    不另抄一份字段样例 —— 抄一份就是第二处「组字段表」。
    """

    def _assert_all_groups_landed(self, card: CharacterCard) -> None:
        """各组各自的代表字段都得在卡上 —— 少一组说明那个组的帧没并进来。"""
        assert card.name == "角色"                                    # G1
        assert card.decision_style == "谨慎型"                         # G2
        assert card.speaking_style.tone == "冷淡"                      # G3
        assert card.key_memories == ["关键经历"]                       # G4
        assert [r.target for r in card.relationships] == ["某人"]        # G5

    def test_bg_task_accumulates_one_card(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm)
        distiller = _card_distiller()

        row = _run_bg(store, user_id, tid, distiller, monkeypatch)

        assert row["status"] == "done", row
        assert len(tm.saved) == 1, f"落库的卡不是一张：{len(tm.saved)}"
        # 落库前 `CharacterCard.model_validate(累加串)` 已过；再拿各组各自的字段核
        # 一遍「每段都并进来了」——只核 name 的话，逐组 yield 走的「首个顶层 {}」退路
        # 也能捞出一张只有 G1 的卡。组名从 `FORMAT_GROUPS` 读，不写死一份并列清单。
        self._assert_all_groups_landed(tm.saved[0])
        assert sorted(distiller._llm.format_groups) == sorted(FORMAT_GROUPS), (
            f"各组没都跑：{distiller._llm.format_groups}"
        )

    def test_sse_accumulates_exactly_one_str_frame(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        client = _build_client(
            store, user_id, monkeypatch, distiller=_card_distiller(), tm=tm)

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        assert r.status_code == 200
        frames = [json.loads(l[len("data: "):]) for l in r.text.splitlines()
                  if l.startswith("data: ")]
        tokens = [f["token"] for f in frames if "token" in f]
        assert len(tokens) == 1, f"成品卡的 str 帧不是恰 1 个：{len(tokens)}"
        self._assert_all_groups_landed(CharacterCard.model_validate(json.loads(tokens[0])))
        # 对话示例是落卡前的后置步骤（WP17），**不在流出的 token 帧里**（那是格式化阶段
        # 的原始 JSON）；落库的那张卡上必须有它 —— 本通道漏接这一步就是静默空示例。
        assert tm.saved[0].dialogue_examples == ["路人：先前的话。\n角色：我说一句话。"]


# ── 7. 预检前移：原文里挑不出对话句 → 长步骤之前失败，不花钱（补充 1-第 4 步） ────


class _RecordingLLM(_FormattingLLM):
    """在 `_FormattingLLM` 上记一笔调用名 —— 本组要看的是「除识别外一个调用都没发生」。

    短正文的识别走 `chat`（单分片，不进归组判定）；Map 走 `async_chat`、一次读完走
    `chat_stream_long`、挑选走 `select_by_schema`。记住方法名就分得出长步骤那三类。
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def chat(self, system, messages, **kw):
        # 识别那一跳不带 max_tokens；带它的是长步骤（分片分析那条），据此分开记。
        self.calls.append("chat" if not kw else "chat+k")
        return super().chat(system, messages)

    async def async_chat(self, *a, **kw):
        self.calls.append("async_chat")
        return await super().async_chat(*a, **kw)

    def chat_stream_long(self, *a, **kw):
        self.calls.append("chat_stream_long")
        return super().chat_stream_long(*a, **kw)

    def select_by_schema(self, *a, **kw):
        self.calls.append("select_by_schema")
        return super().select_by_schema(*a, **kw)


def _no_quote_body() -> str:
    """没有对话引号的正文：识别照给名单，但按编号挑不出本角色的对话句。

    尾巴带 uuid：识别按「正文指纹 + 模型 + 口径版本」缓存，撞上前面用例的缓存就不会
    再走一次识别，`calls` 里连 `chat` 都没有 —— 前提断言会当场拆穿。
    """
    return f"角色说的话，没有引号{uuid.uuid4().hex}"


NO_CANDIDATE_TEXT = "原文里找不到「角色」的对话句"


def _recording_distiller() -> tuple[Distiller, _RecordingLLM]:
    """真 `Distiller` + 记录调用名的假适配器 —— 本组判的正是它内部哪一步先跑。"""
    llm = _RecordingLLM()
    d = Distiller(llm=llm, config_path=None)
    d._longctx_threshold = 0      # 正文短也走分片那条，别让「一次读完」被短文本挡掉
    d._chunk_size = 3000
    return d, llm


def _assert_no_paid_step_ran(llm: _RecordingLLM) -> None:
    """除识别（`chat`）外不该有任何 Map / 一次读完 / 挑选调用。"""
    assert "chat" in llm.calls, f"识别都没跑，本用例没测到预检：{llm.calls}"
    assert [c for c in llm.calls if c != "chat"] == [], (
        f"预检没拦住长步骤，付费调用已经发生：{llm.calls}")


class TestNoCandidateFailsBeforeAnyPaidStep:
    """原文里挑不出本角色的对话句 → 三条通道都在长步骤之前失败（补充 1-第 4 步）。

    失败本身不难（挑选那一步也会抛），难的是**在花钱之前**抛：预检放在别名之后、
    长步骤之前，就是为此。故每条通道都核两件事 —— 失败文案，以及假适配器上没有任何
    长步骤调用。

    变异：把预检删掉或挪到长步骤之后 → 记录里出现 Map / 一次读完 / 挑选，断言红；
    预检即使不删，只要挪到后面，任务照样失败但钱已经花掉（文案断言仍绿、调用断言红）。
    """

    def test_bg(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller, llm = _recording_distiller()

        row = _run_bg(store, user_id, tid, distiller, monkeypatch,
                      body=_no_quote_body())

        assert row["status"] == "error"
        assert NO_CANDIDATE_TEXT in row["message"], row["message"]
        _assert_no_paid_step_ran(llm)

    def test_sse(self, store, user_id, monkeypatch):
        # 正文从库里读（通道读的是 `text_rec["content"]`），不是 `_BODY`。
        tid = _seed_text(store, user_id, body=_no_quote_body())
        distiller, llm = _recording_distiller()
        client = _build_client(store, user_id, monkeypatch, distiller=distiller)

        r = client.post("/api/distill/run_stream",
                        json={"text_id": tid, "character_name": "角色"})

        assert r.status_code == 200
        errs = _sse_errors(r.text)
        assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
        assert NO_CANDIDATE_TEXT in errs[0]["error"], errs[0]["error"]
        _assert_no_paid_step_ran(llm)

    def test_text_manager(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id, body=_no_quote_body())
        distiller, llm = _recording_distiller()
        tm = TextManager(lambda: store, distiller, object(), {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "角色"})

        assert r.status_code == 400, r.text
        assert NO_CANDIDATE_TEXT in r.json()["detail"], r.json()["detail"]
        _assert_no_paid_step_ran(llm)


# ── 8. 两个 async 通道：预检与挑选不阻塞事件循环（补充 1-第 4 步） ────────────

SLEEPY = 0.3


class _SleepyDistiller:
    """`dialogue_candidates` 与 `finalize_card` 各睡 0.3 秒的假蒸馏器。

    两段都必须挪到线程里（`asyncio.to_thread`）：直接在协程里 `time.sleep`，这 0.6 秒
    事件循环停摆，SSE 通道上的其他请求、别的并发调用全被堵住。
    """

    def identify_characters(self, content):
        return [{"name": "角色"}, {"name": "路人"}]

    def dialogue_candidates(self, content, name, aliases=(), roster=()):
        time.sleep(SLEEPY)
        return [object()]

    def distill_incremental(self, content, character_name, aliases=None, **kw):
        return CharacterCard.model_validate({"name": character_name})

    def distill_incremental_stream(self, content, name, aliases=None, **kw):
        yield json.dumps({"name": name}, ensure_ascii=False)

    def finalize_card(self, card, content, name, aliases=(), roster=()):
        time.sleep(SLEEPY)
        card_dict = card.model_dump()
        card_dict["dialogue_examples"] = ["路人：先前的话。\n角色：我说一句话。"]
        return CharacterCard.model_validate(card_dict)


async def _ticks_while(coro) -> tuple[int, float]:
    """跑 `coro`，同时开一个每 10ms 自增的协程；返回（自增次数，两次自增之间的最长间隔）。

    只看总次数分不出「两段睡眠都在线程里」与「只剩一段在线程里」—— 后者仍有半程是
    自由的（0.3 秒约跳 30 次，照样过 ≥10）。故再量最长停顿：任一段挪回协程里同步
    睡眠，停顿就是那一段的 0.3 秒。首段间隔不计（那是任务启动、`coro` 跑到首个 await
    之前的噪声，与事件循环被堵无关）。
    """
    ticks = 0
    last = time.monotonic()
    max_gap = 0.0

    async def _tick():
        nonlocal ticks, last, max_gap
        while True:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            if ticks:
                max_gap = max(max_gap, now - last)
            last = now
            ticks += 1

    t = asyncio.create_task(_tick())
    try:
        await coro
    finally:
        t.cancel()
    return ticks, max_gap


# 同步睡眠 = 0.3 秒整段停摆（远大于这个上限）；线程度过时循环照常跳动（间隔约 0.01 秒）。
# 取 0.8 倍 SLEEPY 作上限：既给调度抖动留 10 倍余量，又能抓住单段同步。
_MAX_STALL = SLEEPY * 0.8


class TestAsyncChannelsDoNotBlockTheEventLoop:
    def test_sse(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        app = _build_app(store, user_id, monkeypatch,
                         distiller=_SleepyDistiller(), tm=_SavingTM())

        async def _drive():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                return await ac.post("/api/distill/run_stream",
                                     json={"text_id": tid, "character_name": "角色"})

        ticks, stall = _run_async(_ticks_while(_drive()))
        assert ticks >= 10, f"预检/挑选把事件循环堵住了：0.6 秒里只跳了 {ticks} 次"
        assert stall < _MAX_STALL, f"有同步阻塞：最长 {stall:.3f} 秒没跳一次"

    def test_text_manager(self, store, user_id):
        tid = _seed_text(store, user_id)
        tm = TextManager(lambda: store, _SleepyDistiller(), object(), {},
                         memory_manager=None)

        ticks, stall = _run_async(_ticks_while(tm.get_or_distill(tid, "角色", user_id)))
        assert ticks >= 10, f"预检/挑选把事件循环堵住了：0.6 秒里只跳了 {ticks} 次"
        assert stall < _MAX_STALL, f"有同步阻塞：最长 {stall:.3f} 秒没跳一次"


# ── 9. 卡片引文核对（WP18）：三条通道落卡前都去掉编造引文的引号 ──────────────

# `_BODY` 里没有这句话：卡片若照原样落库，就是一条冒充原文的编造引文。
FABRICATED = "我从未到过这里"
FABRICATED_MEMORY = f"她说“{FABRICATED}”就再没回来"
RETRACTED_MEMORY = f"她说{FABRICATED}就再没回来"


class _QuotingLLM(_FormattingLLM):
    """G4 的 `key_memories` 换一条**编造**的引文，其余组原样 —— 落卡前必须去引号。

    组模板里的 `关键经历` 只在 G4 出现，替换不波及其它组。替换的是**已序列化**的 JSON
    串（`json.dumps` 默认 `ensure_ascii`），故也用 `json.dumps` 造同一形态的文本再换，
    不手抄一份转义后的 JSON。

    流式（bg / SSE）与非流式（TextManager）两条路径各改一处，共用同一个 `_inject`：
    只改一条的话，另一条通道的用例会因为「卡里压根没有编造引文」而空绿。
    """

    @staticmethod
    def _inject(text: str) -> str:
        return text.replace(json.dumps("关键经历")[1:-1],
                            json.dumps(FABRICATED_MEMORY)[1:-1])

    def chat(self, system, messages, max_tokens=None, **kw):
        return self._inject(
            super().chat(system, messages, max_tokens=max_tokens, **kw))

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
        gen = super().chat_stream_long(system, messages, max_tokens=max_tokens, **kw)
        try:
            while True:
                chunk = next(gen)
                yield self._inject(chunk) if isinstance(chunk, str) else chunk
        except StopIteration as stop:
            return stop.value


def _quoting_distiller() -> Distiller:
    d = Distiller(llm=_QuotingLLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    return d


class TestQuoteRetractionOnEveryChannel:
    """三条产卡通道都走 `Distiller.finalize_card`（WP18）：编造引文的引号在落卡前去掉。

    引文核对与贴对话示例共用同一个后置入口，通道漏接那个入口时，卡上留一条冒充原文的
    引文 —— 成品与「这句确实出自原文」从卡片本身看不出来。三条通道各接一次，各一条用例。

    变异：任一通道改回直接调 `attach_dialogue_examples`（或 `finalize_card` 漏掉核对那
    一步）→ 该通道的 `key_memories` 断言红；顺带核对话示例仍在，防「换入口把前一步丢了」。
    """

    def test_bg_task(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm)

        row = _run_bg(store, user_id, tid, _quoting_distiller(), monkeypatch)

        assert row["status"] == "done", row
        assert tm.saved[0].key_memories == [RETRACTED_MEMORY]
        assert tm.saved[0].dialogue_examples == ["路人：先前的话。\n角色：我说一句话。"]

    def test_sse(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        client = _build_client(
            store, user_id, monkeypatch, distiller=_quoting_distiller(), tm=tm)

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        assert r.status_code == 200
        assert tm.saved[0].key_memories == [RETRACTED_MEMORY]

    def test_text_manager(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller = _quoting_distiller()
        tm = TextManager(lambda: store, distiller, None, {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "角色"})

        assert r.status_code == 200, r.text
        assert r.json()["key_memories"] == [RETRACTED_MEMORY]


# ── 10. 卡片关系去重：三条通道落卡前同一 target 只留第一条 ──────────────────

# G5 样例里那条关系的 target；两条关系 target 相同、relation 不同 —— 正是刘姥姥验收
# 实测的形态（同一个人被拆成两条）。
DUP_TARGET = _SAMPLE_FIELD_VALUES["relationships"][0]["target"]
KEPT_RELATION = "旧识"
DROPPED_RELATION = "同乡"


class _DuplicateRelationshipLLM(_FormattingLLM):
    """G5 的 `relationships` 换成两条**同一 target**、relation 各异的条目，其余组原样 ——
    落卡前必须只剩第一条。

    替换的是**已序列化**的 JSON 串，故用 `json.dumps` 造同一形态的文本再换（同
    `_QuotingLLM`，不手抄转义后的 JSON）。流式（bg / SSE）与非流式（TextManager）两条路径
    各改一处，共用同一个 `_inject`：只改一条的话，另一条通道的用例会因为「卡里压根没有重复
    target」而空绿。
    """

    @staticmethod
    def _inject(text: str) -> str:
        origin = _SAMPLE_FIELD_VALUES["relationships"]
        two = [dict(origin[0], relation=KEPT_RELATION),
               dict(origin[0], relation=DROPPED_RELATION)]
        return text.replace(json.dumps(origin)[1:-1], json.dumps(two)[1:-1])

    def chat(self, system, messages, max_tokens=None, **kw):
        return self._inject(
            super().chat(system, messages, max_tokens=max_tokens, **kw))

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
        gen = super().chat_stream_long(system, messages, max_tokens=max_tokens, **kw)
        try:
            while True:
                chunk = next(gen)
                yield self._inject(chunk) if isinstance(chunk, str) else chunk
        except StopIteration as stop:
            return stop.value


def _dup_rel_distiller() -> Distiller:
    d = Distiller(llm=_DuplicateRelationshipLLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    return d


class TestRelationshipDedupeOnEveryChannel:
    """三条产卡通道都走 `Distiller.finalize_card`：同一 target 的关系条目落卡前只剩第一条。

    重复 target 是刘姥姥验收实测的形态。成品上「两条」与「一条」差别很小，通道漏接后置入口
    时从卡片本身看不出来 —— 三条通道各接一次，各一条用例。

    变异：任一通道改回直接调 `attach_dialogue_examples`（或 `finalize_card` 漏掉去重那一步）
    → 该通道的断言红；顺带核引文核对那一步仍在（`key_memories` 没被丢）。
    """

    def test_bg_task(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm)

        row = _run_bg(store, user_id, tid, _dup_rel_distiller(), monkeypatch)

        assert row["status"] == "done", row
        assert [(r.target, r.relation) for r in tm.saved[0].relationships] == [
            (DUP_TARGET, KEPT_RELATION)]
        assert tm.saved[0].key_memories == ["关键经历"]

    def test_sse(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        tm = _SavingTM()
        client = _build_client(
            store, user_id, monkeypatch, distiller=_dup_rel_distiller(), tm=tm)

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        assert r.status_code == 200
        assert [(rel.target, rel.relation) for rel in tm.saved[0].relationships] == [
            (DUP_TARGET, KEPT_RELATION)]

    def test_text_manager(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller = _dup_rel_distiller()
        tm = TextManager(lambda: store, distiller, None, {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "角色"})

        assert r.status_code == 200, r.text
        assert [(rel["target"], rel["relation"])
                for rel in r.json()["relationships"]] == [(DUP_TARGET, KEPT_RELATION)]
