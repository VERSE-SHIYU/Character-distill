"""时间事件提醒子系统：从记忆中检索到期事件并管理已问状态。"""

from __future__ import annotations

import logging

from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class EventService:
    """管理角色对时间事件的感知：哪些到期了、哪些已经问过。

    「问过没有」是事件自己的状态，只存在事件那条记忆的元数据里（``asked``），不另存记录、
    进程内也不留第二份（spec ``docs/specs/time-event-asked.md``）。事件的身份就是它那条
    记忆的 id。

    状态
    ----
    _injected_event_id : str
        本轮注入 prompt 的事件 id（响应后由 mark_asked 标记为已问）。

    外部依赖
    --------
    memory : 这个引擎的记忆（``ScopedMemory``：视图 + 归属绑在一起），读调用
        ``memory.get_all()`` / 写调用 ``memory.update_metadata()``。时间事件据此按存档隔离。
    """

    ASKED = "asked"

    def __init__(self, memory) -> None:
        self._memory = memory
        self._injected_event_id: str = ""

    def get_due_event(self) -> dict | None:
        """扫描记忆，返回第一个到期且没问过的时间事件（最接近当前时刻的）。"""
        if not self._memory or not self._memory.enabled:
            return None
        try:
            now = datetime.now()
            best = None
            for m in self._memory.get_all():
                meta = m.get("metadata") or {}
                if not isinstance(meta, dict) or meta.get("type") != "time_event" or meta.get(self.ASKED):
                    continue
                eid = m.get("id", "")
                due_at_str = meta.get("due_at", "")
                if not eid or not due_at_str:
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
            logger.warning("Get due event failed: %s", exc, exc_info=True)
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
        """把本轮已注入的事件标记为已问：写在那件事自己的元数据上。写不进去就如实报失败。"""
        eid = self._injected_event_id
        if not eid:
            return
        self._injected_event_id = ""
        if self._memory.update_metadata(eid, {self.ASKED: True}):
            print(f"[ChatEngine] Marked event {eid} as asked")
        else:
            logger.warning("Mark event %s as asked failed; it will be offered again", eid)
