"""时间事件提醒子系统：从记忆中检索到期事件并管理已问状态。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class EventService:
    """管理角色对时间事件的感知：哪些到期了、哪些已经问过。

    状态
    ----
    _asked_event_ids : set[str]
        已问过的事件 ID（会话级，避免本轮重复问）。
    _injected_event_id : str
        本轮注入 prompt 的事件 ID（响应后由 mark_asked 标记为已问）。

    外部依赖
    --------
    memory : 只读调用 memory.get_all() / memory.add_manual()
    card_id : 当前角色卡 ID 字符串
    """

    def __init__(self, memory, card_id: str) -> None:
        self._memory = memory
        self._card_id = card_id
        self._asked_event_ids: set[str] = set()
        self._injected_event_id: str = ""

    def get_due_event(self) -> dict | None:
        """扫描记忆，返回第一个到期的未问时间事件（最接近当前时刻的）。"""
        if not self._memory or not self._memory.enabled or not self._card_id:
            return None
        try:
            memories = self._memory.get_all(self._card_id)
            if not memories:
                return None

            asked_ids = set()
            for m in memories:
                meta = m.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("type") == "time_event_asked":
                    asked_ids.add(meta.get("event_id", ""))

            now = datetime.now()
            best = None
            for m in memories:
                meta = m.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                if meta.get("type") != "time_event":
                    continue
                eid = meta.get("event_id", "")
                if not eid or eid in asked_ids or eid in self._asked_event_ids:
                    continue
                due_at_str = meta.get("due_at", "")
                if not due_at_str:
                    continue
                try:
                    due_at = datetime.fromisoformat(due_at_str)
                except (ValueError, TypeError):
                    continue
                if due_at > now:
                    continue
                if best is None or due_at > best["due_at"]:
                    best = {
                        "event": meta.get("event", ""),
                        "when_text": meta.get("when_text", ""),
                        "event_id": eid,
                        "due_at": due_at,
                    }
            return best
        except Exception as exc:
            print(f"[ChatEngine] _get_due_event failed: {exc}")
            return None

    def build_candidate_block(self) -> str:
        """如果记忆中有到期的未问事件，返回可选关心提示片段。"""
        pending = self.get_due_event()
        if not pending:
            self._injected_event_id = ""
            return ""
        self._injected_event_id = pending["event_id"]
        return (
            "\n\n【你或许记着的一件事】\n"
            f"对方之前提到「{pending['event']}」，时间大约在「{pending['when_text']}」，"
            f"现在应该已经发生/到时间了。\n"
            "如果这轮气氛自然，你可以像真人一样【关心地】问起——但要符合你的性格："
            "含蓄的人就旁敲侧击，热情的人就直接问。"
            "绝不要机械复述细节，要带着在意去问。"
            "如果这轮在吵架/对方情绪差/话题不搭，就先不提。\n"
        )

    def mark_asked(self) -> None:
        """将本轮已注入的事件标记为已问（持久化标记）。"""
        eid = self._injected_event_id
        if not eid:
            return
        self._injected_event_id = ""
        self._asked_event_ids.add(eid)
        if self._memory and self._memory.enabled and self._card_id:
            try:
                self._memory.add_manual(
                    "",
                    self._card_id,
                    metadata={"type": "time_event_asked", "event_id": eid},
                )
                print(f"[ChatEngine] Marked event {eid} as asked")
            except Exception as exc:
                print(f"[ChatEngine] Mark event asked failed: {exc}")
