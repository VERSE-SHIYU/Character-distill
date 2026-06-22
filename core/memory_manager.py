"""Mem0 长期记忆管理器。"""

from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 加权检索常量（Generative Agents 风格多信号打分）──
RERANK_ALPHA = 0.45    # 语义相关性权重
RERANK_BETA  = 0.15    # 时间新近度权重
RERANK_GAMMA = 0.25    # 重要性权重
RERANK_DELTA = 0.15    # 情绪契合度权重
RECENCY_TAU_HOURS = 72 # 指数衰减时间常数（小时）

# ── 情绪极性关键词（子串匹配，覆盖 LLM 丰富情绪词）──
_POSITIVE_KEYWORDS = [
    "开心", "喜悦", "高兴", "快乐", "幸福", "甜蜜", "心动", "期待", "好奇",
    "温柔", "安心", "放心", "欣慰", "感激", "感动", "满足", "骄傲", "自豪",
    "兴奋", "放松", "惬意", "释然", "喜欢", "上头", "心软",
]
_NEGATIVE_KEYWORDS = [
    "烦乱", "愤怒", "暴怒", "生气", "恼火", "烦躁", "焦虑", "不安", "紧张",
    "委屈", "吃味", "嫉妒", "失落", "伤心", "难过", "悲伤", "痛苦", "绝望",
    "恐惧", "害怕", "防备", "警觉", "厌恶", "嫌弃", "无奈", "疲惫", "冷淡",
    "疏离", "不屑", "心碎", "背叛", "刺痛", "又气", "心痛", "心如刀绞",
    "自毁", "恨",
]

def _get_emotion_polarity(mood: str) -> int:
    """返回情绪极性：1=正面, -1=负面, 0=中性/未知。

    使用关键词子串匹配——LLM 产生的情绪词变化繁多（如"暴怒中混杂着被无视的刺痛"），
    精确匹配覆盖率太低。子串匹配按正面/负面关键词命中数多数决，平局则判定为中性。
    """
    if not mood:
        return 0
    m = mood.strip()
    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in m)
    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in m)
    if pos_hits > neg_hits:
        return 1
    if neg_hits > pos_hits:
        return -1
    return 0

def _emotion_match(current_mood: str | None, memory_mood: str) -> float:
    """情绪契合度：同极性=1.0, 一正一负=0.0, 涉及中性/未知=0.5。"""
    if current_mood is None:
        return 0.5
    cur_p = _get_emotion_polarity(current_mood)
    mem_p = _get_emotion_polarity(memory_mood)
    if cur_p == 0 or mem_p == 0:
        return 0.5
    return 1.0 if cur_p == mem_p else 0.0


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
                        "model": "deepseek-chat",
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

    def search(self, query: str, card_id: str, current_mood: str | None = None) -> list[dict[str, Any]]:
        """检索长期记忆，返回结构化 dict 列表（经加权重排）。

        每条返回: {text, relevance, importance, age_seconds}
        加权公式: final = α·relevance_norm + β·recency + γ·importance_norm + δ·emotion_match
        current_mood 为 None 时 emotion_match=0.5（退化，向后兼容）。
        """
        if not self.enabled:
            return []
        try:
            results = self._mem.search(
                query, filters={"user_id": card_id}, limit=self._search_top_k
            )
            if isinstance(results, dict):
                results = results.get("results", [])
        except Exception as exc:
            print(f"[MemoryManager] Search failed: {exc}")
            return []

        now = datetime.now(timezone.utc)
        scored: list[dict[str, Any]] = []

        for r in results:
            if not isinstance(r, dict):
                continue
            text = r.get("memory", "").strip()
            if not text:
                continue

            relevance = float(r.get("score", 0.5) or 0.5)

            meta = r.get("metadata") or {}
            importance_raw = meta.get("importance", 5) if isinstance(meta, dict) else 5
            importance = max(1, min(10, int(importance_raw)))
            memory_mood = meta.get("mood", "") if isinstance(meta, dict) else ""

            created_str = r.get("created_at", "")
            age_seconds = 0.0
            if created_str:
                try:
                    created = datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
                    age_seconds = (now - created).total_seconds()
                except (ValueError, TypeError):
                    pass

            scored.append({
                "text": text,
                "relevance": relevance,
                "importance": importance,
                "age_seconds": age_seconds,
                "memory_mood": memory_mood,
            })

        if not scored:
            return []

        # 归一化 relevance 到 0-1（Mem0 score 可能不在这个范围）
        rels = [s["relevance"] for s in scored]
        rel_min, rel_max = min(rels), max(rels)
        rel_range = rel_max - rel_min if rel_max > rel_min else 1.0

        # 计算 final 加权分并排序
        for s in scored:
            relevance_norm = (s["relevance"] - rel_min) / rel_range
            age_hours = s["age_seconds"] / 3600.0
            recency = math.exp(-age_hours / RECENCY_TAU_HOURS)
            importance_norm = s["importance"] / 10.0
            emo_match = _emotion_match(current_mood, s["memory_mood"])
            s["final"] = (
                RERANK_ALPHA * relevance_norm
                + RERANK_BETA * recency
                + RERANK_GAMMA * importance_norm
                + RERANK_DELTA * emo_match
            )
            s["emo_match"] = emo_match

        scored.sort(key=lambda s: s["final"], reverse=True)
        top = scored[: self._search_top_k]
        if top:
            summary = ", ".join(
                f"imp={m['importance']} emo={m['emo_match']:.1f} final={m['final']:.3f}" for m in top[:3]
            )
            mood_tag = f"mood={current_mood}" if current_mood else "mood=None"
            print(f"[MemoryManager] search top-{len(top)} ({mood_tag}): {summary}")
        return top

    def add(self, messages: list[dict[str, str]], card_id: str, metadata: dict | None = None) -> None:
        """将对话消息写入长期记忆（后台异步执行）。metadata 写入 Mem0 存储供检索加权。"""
        if not self.enabled:
            return

        print(f"[MemoryManager] add called: card={card_id} msg_count={len(messages)} metadata={metadata}")

        def _do_add():
            try:
                kwargs = {"user_id": card_id}
                if metadata:
                    kwargs["metadata"] = metadata
                result = self._mem.add(messages, **kwargs)
                print(f"[MemoryManager] add OK: card={card_id} result_len={len(result) if isinstance(result, list) else 'N/A'}")
            except Exception as exc:
                print(f"[MemoryManager] Add failed: {exc}")
                import traceback
                traceback.print_exc()

        threading.Thread(target=_do_add, daemon=True).start()

    def get_all(self, card_id: str) -> list[dict[str, Any]]:
        """获取某角色的所有记忆。"""
        if not self.enabled:
            return []
        try:
            results = self._mem.get_all(filters={"user_id": card_id})
            if isinstance(results, dict):
                results = results.get("results", [])
            return results
        except Exception as exc:
            print(f"[MemoryManager] Get all failed: {exc}")
            return []

    def add_manual(self, text: str, card_id: str) -> bool:
        """手动添加一条单文本记忆。infer=False 避免 Mem0 LLM 提炼丢弃。"""
        if not self.enabled:
            return False
        try:
            result = self._mem.add(text, user_id=card_id, infer=False)
            print(f"[MemoryManager] manual add result: {result}")
            return True
        except Exception as exc:
            print(f"[MemoryManager] manual add failed: {exc}")
            return False

    def update(self, memory_id: str, text: str) -> bool:
        """更新一条记忆的内容。"""
        if not self.enabled:
            return False
        try:
            self._mem.update(memory_id=memory_id, data=text)
            return True
        except Exception as exc:
            print(f"[MemoryManager] update failed: {exc}")
            return False

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
