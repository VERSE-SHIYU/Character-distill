"""三层隔离好感评估管道：CORE 状态机 ← 可失败副作用物理隔离。

CORE 层失败 → return EvalResult(applied=False)，不触碰持久化。
SIDE-EFFECT 层每步独立 try/except，互不影响，永不回滚 CORE 状态。
"""

from __future__ import annotations

import logging

import time
import uuid
from dataclasses import dataclass
from typing import Any

from core import session_record as record_protocol
from core.session_record import SessionRecord
from core.utils import try_record_usage

logger = logging.getLogger(__name__)


@dataclass
class EvalContext:
    """Pipeline 只读输入包。ChatEngine 负责组装，pipeline 不反向依赖 ChatEngine。"""
    card: Any
    user_message: str
    assistant_reply: str
    user_role: str
    old_stage: str
    session_id: str
    group_id: str
    card_id: str
    storage: Any
    memory: Any
    affinity_service: Any
    reaction_service: Any
    llm: Any
    reaction_appraisal: str = ""
    departure_notice: str = ""
    # 一对一存档的本场记录；群聊（或没有存档）为 None —— 不抽、不存、提示词不变。
    session_record: SessionRecord | None = None


@dataclass
class EvalResult:
    """Pipeline 返回值。applied=False 表示 CORE 层未执行（异常/跳过）。"""
    importance: int = 5
    affinity: int = 50
    stage_upgraded: bool = False
    applied: bool = False
    in_character: int = 80  # 瞬时信号，不入库，不跨 session
    ooc_reason: str = ""
    assertion_confidence: int = 50  # 用户输入可信度，用于记忆写入过滤
    session_record: SessionRecord | None = None  # 应用本轮操作后的本场记录（一对一）


class EvaluationPipeline:
    """三层好感评估管道。

    用法
    ----
    pipeline = EvaluationPipeline()
    result = pipeline.run(ctx)
    """

    def __init__(self) -> None:
        # 本场记录抽取用的低温适配器，按「派生自哪个适配器」缓存：派生会新建客户端，
        # 不能每轮建；引擎换了适配器（换 key）就重新派生。
        self._record_llm_src: Any = None
        self._record_llm: Any = None

    def run(self, ctx: EvalContext) -> EvalResult:
        """执行完整管道。

        1. 好感评估（`_run_affinity`）：CORE 层 LLM 调用 → JSON 解析 → 状态回写 → 落库
        2. 本场记录（`_run_session_record`，只在一对一）：**单独一次** LLM 调用 → 校验 → 有变化才落库
        两步各是各的失败域：哪一步失败都不影响另一步。
        """
        result = self._run_affinity(ctx)
        result.session_record = self._run_session_record(ctx)
        return result

    def _run_affinity(self, ctx: EvalContext) -> EvalResult:
        """好感评估：任一 CORE 失败 → EvalResult(applied=False, importance=5)；副作用各自 try。"""
        # ── CORE layer ─────────────────────────────────────────
        data = self._core_evaluate(ctx)
        if data is None:
            print(f"[EvaluationPipeline] RUN SKIP session={ctx.session_id}: _core_evaluate returned None")
            return EvalResult(importance=5, applied=False)

        importance = ctx.affinity_service.apply_evaluation(data, ctx.old_stage, ctx.card.psyche)
        # 瞬时纠偏信号：只传递，不入库，不跨 session
        in_character = data.get("in_character", 80)
        if not isinstance(in_character, int) or in_character < 0 or in_character > 100:
            in_character = 80
        ooc_reason = data.get("ooc_reason", "")
        # 用户输入可信度：用于记忆写入过滤，与好感/出戏正交
        assertion_confidence = data.get("assertion_confidence", 50)
        if not isinstance(assertion_confidence, int) or assertion_confidence < 0 or assertion_confidence > 100:
            assertion_confidence = 50
        # 到此核心状态已落定，下面任何异常都不得回滚

        # ── SIDE-EFFECT layer（全部 fire-and-forget）─────────────
        self._persist_time_event(ctx, data)
        self._persist_affinity(ctx)

        return EvalResult(
            importance=importance,
            affinity=ctx.affinity_service.affinity,
            stage_upgraded=ctx.affinity_service.stage_upgraded,
            in_character=in_character,
            ooc_reason=ooc_reason,
            assertion_confidence=assertion_confidence,
            applied=True,
        )

    # ── CORE ──────────────────────────────────────────────────

    def _core_evaluate(self, ctx: EvalContext) -> dict | None:
        """CORE 子步骤：构建 prompt → LLM 调用（1次重试）→ JSON 解析。

        返回解析后的 data dict，或 None（表示跳过/失败）。
        任何异常在此层捕获，不泄露到外层。
        """
        try:
            prompt = ctx.affinity_service.build_evaluation_prompt(
                ctx.card, ctx.user_message, ctx.assistant_reply,
                ctx.user_role, ctx.reaction_appraisal,
                departure_notice=ctx.departure_notice,
            )
            # 1次自动重试（间隔2s），应对瞬时 LLM 连接故障
            reply = None
            for attempt in range(2):
                try:
                    reply = ctx.llm.chat(
                        "你是精确的JSON输出器，只输出JSON。",
                        [{"role": "user", "content": prompt}],
                    )
                    try_record_usage(ctx.storage, ctx.llm,
                                     action="chat_affinity_eval", source="EvaluationPipeline")
                    break
                except Exception as exc:
                    if attempt == 0:
                        logger.warning("LLM call failed (attempt 1/2), retrying in 2s session=%s: %s",
                                   ctx.session_id, exc, exc_info=True)
                        time.sleep(2)
                    else:
                        raise
            data = ctx.affinity_service.parse_evaluation_reply(reply)
            if data is None:
                logger.warning(
                    "Affinity eval produced no JSON object session=%s "
                    "(first 200 chars): %r", ctx.session_id, reply[:200],
                )
                return None
            return data
        except Exception as exc:
            logger.warning("Affinity eval failed session=%s: %s", ctx.session_id, exc, exc_info=True)
            return None

    # ── 本场记录（一对一）─────────────────────────────────────

    def _run_session_record(self, ctx: EvalContext) -> SessionRecord | None:
        """单独一次抽取调用更新本场记录；有变化才落库。群聊（record 为 None）不调用、不存。

        失败（调用、解析、校验抛错）只丢这一轮的本场记录更新，记录原样返回，好感不受影响。
        重试由适配器自己的预算负责（`LLMAdapter.chat`），这里不再套一层。
        """
        if ctx.session_record is None:
            return None

        def call(system: str, user: str) -> str:
            llm = self._record_llm_for(ctx.llm)
            reply = llm.chat(system, [{"role": "user", "content": user}])
            try_record_usage(ctx.storage, llm,
                             action="chat_session_record", source="EvaluationPipeline")
            return reply

        try:
            record, warns = record_protocol.extract(
                ctx.session_record, char_name=ctx.card.name, user_message=ctx.user_message,
                assistant_reply=ctx.assistant_reply, call=call)
        except Exception as exc:  # 本场记录坏了不能拖累别的
            logger.warning("Session record update failed, record unchanged (session=%s): %r",
                           ctx.session_id, exc, exc_info=True)
            return ctx.session_record
        for w in warns:
            logger.warning("Session record (session=%s): %s", ctx.session_id, w)
        if record.entries == ctx.session_record.entries:
            return ctx.session_record
        self._persist_session_record(ctx, record)
        return record

    def _record_llm_for(self, llm: Any) -> Any:
        if llm is not self._record_llm_src:
            self._record_llm = llm.derive(temperature=record_protocol.EXTRACT_TEMPERATURE)
            self._record_llm_src = llm
        return self._record_llm

    # ── SIDE-EFFECT 1: 时间事件 ───────────────────────────────

    def _persist_time_event(self, ctx: EvalContext, data: dict) -> None:
        """从 LLM data 中抽取 time_event 并写入 Mem0。"""
        time_event = data.get("time_event")
        if not (time_event and isinstance(time_event, dict)
                and ctx.memory and ctx.memory.enabled):
            return
        try:
            evt = time_event.get("event", "")
            when_text = time_event.get("when_text", "")
            due_at = time_event.get("due_at", "")
            event_id = uuid.uuid4().hex[:16]
            ctx.memory.add_manual(
                f"对方提到「{evt}」（{when_text}），大约在{due_at}。",
                metadata={
                    "type": "time_event",
                    "event_id": event_id,
                    "event": evt,
                    "when_text": when_text,
                    "due_at": due_at,
                },
            )
            print(f"[EvaluationPipeline] Saved time_event: {evt} at {due_at} (id={event_id})")
        except Exception as exc:
            logger.warning("Save time_event failed: %s", exc, exc_info=True)

    # ── SIDE-EFFECT 2: 好感持久化 ─────────────────────────────

    def _persist_affinity(self, ctx: EvalContext) -> None:
        """将 AffinityService 中的最新好感状态写回 DB。

        群聊走 update_group_affinity，单聊走 save_affinity_state。
        群聊没有 session_id 时跳过（仅内存模式）。
        """
        if not ctx.storage:
            return

        svc = ctx.affinity_service
        if ctx.group_id:
            try:
                from core.scheduling import submit_to_main_loop
                import time as _t
                _t0 = _t.time()
                submit_to_main_loop(
                    ctx.storage.update_group_affinity(
                        ctx.group_id, ctx.card_id,
                        svc.affinity, svc.trust,
                        svc.mood, svc.guard, svc.affinity_reason,
                    ),
                    timeout=15,
                )
                print(f"[EvaluationPipeline] Group affinity saved ({_t.time()-_t0:.2f}s)")
            except Exception as exc:
                logger.warning("Group affinity save failed (group=%s card=%s): %s",
                                   ctx.group_id, ctx.card_id, exc, exc_info=True)
        elif ctx.session_id:
            # 一对一每轮好感的唯一保存点（引擎评估完不再另存一次）
            try:
                from core.scheduling import submit_to_main_loop
                import time as _t
                _t0 = _t.time()
                submit_to_main_loop(
                    ctx.storage.save_affinity_state(
                        ctx.session_id, svc.to_persist(),
                    ),
                    timeout=15,
                )
                print(f"[EvaluationPipeline] Session affinity state saved ({_t.time()-_t0:.2f}s)")
            except Exception as exc:
                logger.warning("Session affinity state save failed (session=%s): %s",
                                   ctx.session_id, exc, exc_info=True)

    # ── SIDE-EFFECT 3: 本场记录持久化（只在本轮有变化时）────────────

    def _persist_session_record(self, ctx: EvalContext, record: SessionRecord) -> None:
        if not (ctx.storage and ctx.session_id):
            return
        try:
            from core.scheduling import submit_to_main_loop
            submit_to_main_loop(
                ctx.storage.save_session_record(ctx.session_id, record.to_persist()),
                timeout=15,
            )
        except Exception as exc:
            logger.warning("Session record save failed (session=%s): %s",
                           ctx.session_id, exc, exc_info=True)
