"""三层隔离好感评估管道：CORE 状态机 ← 可失败副作用物理隔离。

CORE 层失败 → return EvalResult(applied=False)，不触碰持久化。
SIDE-EFFECT 层每步独立 try/except，互不影响，永不回滚 CORE 状态。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


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


class EvaluationPipeline:
    """三层好感评估管道。

    用法
    ----
    pipeline = EvaluationPipeline()
    result = pipeline.run(ctx)
    """

    def run(self, ctx: EvalContext) -> EvalResult:
        """执行完整管道。

        1. CORE 层：LLM 调用 → JSON 解析 → 状态回写
           任一失败 → return EvalResult(applied=False, importance=5)
        2. SIDE-EFFECT 1：时间事件持久化（独立 try）
        3. SIDE-EFFECT 2：好感持久化到 DB（独立 try）
        """
        # ── CORE layer ─────────────────────────────────────────
        data = self._core_evaluate(ctx)
        if data is None:
            return EvalResult(importance=5, applied=False)

        importance = ctx.affinity_service.apply_evaluation(data, ctx.old_stage)
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
        """CORE 子步骤：构建 prompt → LLM 调用 → JSON 解析。

        返回解析后的 data dict，或 None（表示跳过/失败）。
        任何异常在此层捕获，不泄露到外层。
        """
        try:
            prompt = ctx.affinity_service.build_evaluation_prompt(
                ctx.card, ctx.user_message, ctx.assistant_reply,
                ctx.user_role, ctx.reaction_appraisal,
                departure_notice=ctx.departure_notice,
            )
            reply = ctx.llm.chat(
                "你是精确的JSON输出器，只输出JSON。",
                [{"role": "user", "content": prompt}],
            )
            data = ctx.affinity_service.parse_evaluation_reply(reply)
            if data is None:
                print("[EvaluationPipeline] FAIL: no JSON object found in LLM reply")
                return None
            return data
        except Exception as exc:
            print(f"[EvaluationPipeline] CORE layer failed: {exc}")
            import traceback
            traceback.print_exc()
            return None

    # ── SIDE-EFFECT 1: 时间事件 ───────────────────────────────

    def _persist_time_event(self, ctx: EvalContext, data: dict) -> None:
        """从 LLM data 中抽取 time_event 并写入 Mem0。"""
        time_event = data.get("time_event")
        if not (time_event and isinstance(time_event, dict)
                and ctx.memory and ctx.memory.enabled and ctx.card_id):
            return
        try:
            evt = time_event.get("event", "")
            when_text = time_event.get("when_text", "")
            due_at = time_event.get("due_at", "")
            event_id = uuid.uuid4().hex[:16]
            ctx.memory.add_manual(
                f"对方提到「{evt}」（{when_text}），大约在{due_at}。",
                ctx.card_id,
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
            print(f"[EvaluationPipeline] Save time_event failed: {exc}")

    # ── SIDE-EFFECT 2: 好感持久化 ─────────────────────────────

    def _persist_affinity(self, ctx: EvalContext) -> None:
        """将 AffinityService 中的最新好感状态写回 DB。

        群聊走 update_group_affinity，单聊走 update_session_affinity。
        群聊没有 session_id 时跳过（仅内存模式）。
        """
        if not ctx.storage:
            return

        svc = ctx.affinity_service
        if ctx.group_id:
            try:
                from deps import run_on_main_loop
                import time as _t
                _t0 = _t.time()
                run_on_main_loop(
                    ctx.storage.update_group_affinity(
                        ctx.group_id, ctx.card_id,
                        svc.affinity, svc.trust,
                        svc.mood, svc.guard, svc.affinity_reason,
                    ),
                    timeout=15,
                )
                print(f"[EvaluationPipeline] Group affinity saved ({_t.time()-_t0:.2f}s)")
            except Exception as exc:
                print(f"[EvaluationPipeline] Group affinity save failed (group={ctx.group_id} card={ctx.card_id}): {exc}")
        elif ctx.session_id:
            try:
                from deps import run_on_main_loop
                import time as _t
                _t0 = _t.time()
                run_on_main_loop(
                    ctx.storage.update_session_affinity(
                        ctx.session_id,
                        svc.affinity, svc.trust,
                        svc.mood, svc.guard, svc.affinity_reason,
                    ),
                    timeout=15,
                )
                print(f"[EvaluationPipeline] Session affinity saved ({_t.time()-_t0:.2f}s)")
            except Exception as exc:
                print(f"[EvaluationPipeline] Session affinity save failed (session={ctx.session_id}): {exc}")
