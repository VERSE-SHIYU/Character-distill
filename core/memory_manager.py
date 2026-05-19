"""Mem0 长期记忆管理器。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


class MemoryManager:
    """封装 Mem0 Memory 实例，提供角色级别的记忆读写。

    每个角色（card_id）有独立的记忆空间，跨会话持久化。
    LLM 使用 DeepSeek v4 Pro，Embedding 使用本地 sentence-transformers。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self._enabled = config.get("enabled", True)
        self._search_top_k = config.get("search_top_k", 10)
        self._context_window = config.get("context_window", 30)
        self._mem: Any = None  # mem0.Memory, lazy-imported
        self._lock = threading.Lock()

        if not self._enabled:
            return

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("[MemoryManager] DEEPSEEK_API_KEY not set — Mem0 disabled")
            self._enabled = False
            return

        try:
            from mem0 import Memory

            repo_root = Path(__file__).resolve().parent.parent
            db_path = str(repo_root / "data" / "mem0_db")

            mem0_config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": db_path,
                        "on_disk": True,
                        "embedding_model_dims": 384,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": "deepseek-v4-pro",
                        "api_key": api_key,
                        "openai_base_url": "https://api.deepseek.com/v1",
                    },
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {
                        "model": "sentence-transformers/all-MiniLM-L6-v2",
                        "embedding_dims": 384,
                    },
                },
            }
            self._mem = Memory.from_config(mem0_config)
            print("[MemoryManager] Mem0 initialized (DeepSeek + local embeddings)")
        except Exception as exc:
            print(f"[MemoryManager] Mem0 init failed: {exc}")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._mem is not None

    @property
    def context_window(self) -> int:
        return self._context_window

    def search(self, query: str, card_id: str) -> list[str]:
        """检索与 query 相关的长期记忆。"""
        if not self.enabled:
            return []
        try:
            results = self._mem.search(
                query, filters={"user_id": card_id}, limit=self._search_top_k
            )
            if isinstance(results, dict):
                results = results.get("results", [])
            memories = []
            for r in results:
                text = r.get("memory", "") if isinstance(r, dict) else str(r)
                if text.strip():
                    memories.append(text.strip())
            return memories
        except Exception as exc:
            print(f"[MemoryManager] Search failed: {exc}")
            return []

    def add(self, messages: list[dict[str, str]], card_id: str) -> None:
        """将对话消息写入长期记忆（后台异步执行）。"""
        if not self.enabled:
            return

        def _do_add():
            try:
                self._mem.add(messages, user_id=card_id)
            except Exception as exc:
                print(f"[MemoryManager] Add failed: {exc}")

        threading.Thread(target=_do_add, daemon=True).start()

    def get_all(self, card_id: str) -> list[dict[str, Any]]:
        """获取某角色的所有记忆。"""
        if not self.enabled:
            return []
        try:
            results = self._mem.get_all(user_id=card_id)
            if isinstance(results, dict):
                results = results.get("results", [])
            return results
        except Exception as exc:
            print(f"[MemoryManager] Get all failed: {exc}")
            return []

    def delete(self, memory_id: str) -> bool:
        """删除单条记忆。"""
        if not self.enabled:
            return False
        try:
            self._mem.delete(memory_id)
            return True
        except Exception as exc:
            print(f"[MemoryManager] Delete failed: {exc}")
            return False

    def delete_all(self, card_id: str) -> bool:
        """清空某角色的全部记忆。"""
        if not self.enabled:
            return False
        try:
            self._mem.delete_all(user_id=card_id)
            return True
        except Exception as exc:
            print(f"[MemoryManager] Delete all failed: {exc}")
            return False
