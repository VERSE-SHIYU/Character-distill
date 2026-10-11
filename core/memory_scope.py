"""长期记忆的归属：一次一对一存档、一个群聊、或整张卡。

方案 C（`docs/specs/memory-archive-scope.md` §2）：把「记忆视图 + 归属」绑成一个
对象（``ScopedMemory``）交给引擎与各服务，调用点不再分别传卡 id 与范围 —— 少一个
可以漏传、漏了就悄悄退回整张卡的参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """这条记忆属于谁。不可变值对象；把归属翻成 mem0 的过滤条件只此一处。

    mem0 用 ``user_id`` + ``run_id`` 定位：卡放 ``user_id``，存档 / 群放 ``run_id``。
    范围值带类型前缀（``session:`` / ``group:``），因为两种 id 同格式（spec §4-F5），
    不带前缀分不出来。
    """

    card_id: str
    run_id: str | None

    @classmethod
    def archive(cls, card_id: str, session_id: str) -> MemoryScope:
        """一对一存档。"""
        return cls(card_id, f"session:{session_id}")

    @classmethod
    def group(cls, card_id: str, group_id: str) -> MemoryScope:
        """群聊。"""
        return cls(card_id, f"group:{group_id}")

    @classmethod
    def whole_card(cls, card_id: str) -> MemoryScope:
        """整张卡（不带范围）—— 只给删除用。"""
        return cls(card_id, None)

    def filters(self) -> dict[str, str]:
        """翻成 mem0 的读 / 写 / 删过滤条件。"""
        f = {"user_id": self.card_id}
        if self.run_id:
            f["run_id"] = self.run_id
        return f


class ScopedMemory:
    """这个引擎的记忆：一个记忆视图 + 一个归属，绑在一起。

    对外不带卡 id、不带范围 —— 卡与范围都由 ``scope`` 带着，没有「漏传」这件事。

    视图为 None（没配 key）、或归属为 None（引擎既无存档 id 也无群 id）时 ``enabled``
    为假；引擎原有的「记忆为空即跳过」分支就是「不提供记忆」。
    """

    def __init__(self, view: Any, scope: MemoryScope | None) -> None:
        self._view = view
        self._scope = scope

    @property
    def enabled(self) -> bool:
        return (self._view is not None
                and getattr(self._view, "enabled", False)
                and self._scope is not None)

    def rebind(self, view: Any) -> None:
        """换视图（用户存了新 key 后由 ``deps`` 调）。归属不动 —— 视图按用户缓存、
        多个存档共用（§4-F3），换视图换的只是「用谁的 key 出站」。"""
        self._view = view

    def search(self, query: str, current_mood: str | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        return self._view.search(query, self._scope, current_mood=current_mood)

    def add(self, messages: list[dict[str, str]], metadata: dict | None = None) -> None:
        if not self.enabled:
            return
        self._view.add(messages, self._scope, metadata=metadata)

    def get_all(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        return self._view.get_all(self._scope)

    def add_manual(self, text: str, metadata: dict | None = None) -> bool:
        if not self.enabled:
            return False
        return self._view.add_manual(text, self._scope, metadata=metadata)

    def reflect(self, llm: Any, recent: list[dict], char_name: str, storage: Any = None) -> None:
        if not self.enabled:
            return
        self._view.reflect(self._scope, llm, recent, char_name, storage=storage)
