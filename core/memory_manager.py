"""Mem0 长期记忆管理器。"""

from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 加权检索常量（两级门控: base = α·rel + β·rec + γ·imp, final = base × (1+λ·emo)）──
RERANK_ALPHA = 0.60    # 语义相关性权重（含原 DELTA 并入）
RERANK_BETA  = 0.15    # 时间新近度权重
RERANK_GAMMA = 0.25    # 重要性权重
RERANK_LAMBDA = 0.25   # 情绪提亮乘性增益上界（final 最多放大 1.25×, 语义无关项不被抬高）
RECENCY_TAU_HOURS = 72 # 指数衰减时间常数（小时）
REFLECTION_THRESHOLD = 30   # 累计重要性达此值（且满足双条件）触发反思
REFLECTION_MIN_ROUNDS = 8   # 距上次反思最少轮数，防高频触发
REFLECTION_MIN_QUALITY = 3  # 触发反思最少需要的高质量（importance>=7）原始记忆条数

# ── 情绪极性关键词（子串匹配，覆盖 LLM 丰富情绪词）──
_POSITIVE_KEYWORDS = [
    "开心", "喜悦", "高兴", "快乐", "幸福", "甜蜜", "心动", "期待", "好奇",
    "温柔", "安心", "放心", "欣慰", "感激", "感动", "满足", "骄傲", "自豪",
    "兴奋", "放松", "惬意", "释然", "窃喜", "喜欢", "上头", "心软",
]
_NEGATIVE_KEYWORDS = [
    "烦乱", "愤怒", "暴怒", "生气", "恼火", "烦躁", "焦虑", "不安", "紧张",
    "委屈", "吃味", "嫉妒", "失落", "伤心", "难过", "悲伤", "痛苦", "绝望",
    "恐惧", "害怕", "防备", "警觉", "厌恶", "嫌弃", "无奈", "疲惫", "冷淡",
    "疏离", "不屑", "心碎", "背叛", "刺痛", "又气", "心痛", "心如刀绞",
    "自毁", "恨",
]

# ── VAD 情绪映射表（valence∈[-1,1], arousal∈[0,1], 子串命中聚合｜零模型）──
_VAD_MAP: dict[str, tuple[float, float]] = {
    # 正面
    "开心": (0.80, 0.70), "喜悦": (0.90, 0.60), "高兴": (0.80, 0.60),
    "快乐": (0.85, 0.60), "幸福": (0.90, 0.40), "甜蜜": (0.80, 0.30),
    "心动": (0.70, 0.80), "期待": (0.50, 0.70), "好奇": (0.40, 0.60),
    "温柔": (0.70, 0.20), "安心": (0.60, 0.20), "放心": (0.60, 0.15),
    "欣慰": (0.70, 0.30), "感激": (0.80, 0.50), "感动": (0.80, 0.60),
    "满足": (0.70, 0.30), "骄傲": (0.60, 0.50), "自豪": (0.60, 0.50),
    "兴奋": (0.80, 0.90), "放松": (0.70, 0.10), "惬意": (0.70, 0.15),
    "释然": (0.50, 0.10), "窃喜": (0.60, 0.40), "喜欢": (0.80, 0.60), "上头": (0.60, 0.90),
    "心软": (0.40, 0.30),
    # 负面
    "烦乱": (-0.50, 0.70), "愤怒": (-0.90, 0.90), "暴怒": (-0.95, 0.95),
    "生气": (-0.70, 0.70), "恼火": (-0.60, 0.70), "烦躁": (-0.50, 0.70),
    "焦虑": (-0.50, 0.80), "不安": (-0.40, 0.60), "紧张": (-0.30, 0.70),
    "委屈": (-0.50, 0.30), "吃味": (-0.30, 0.40), "嫉妒": (-0.50, 0.60),
    "失落": (-0.50, 0.20), "伤心": (-0.70, 0.30), "难过": (-0.60, 0.30),
    "悲伤": (-0.80, 0.30), "痛苦": (-0.90, 0.50), "绝望": (-0.95, 0.20),
    "恐惧": (-0.80, 0.90), "害怕": (-0.70, 0.80), "防备": (-0.20, 0.60),
    "警觉": (-0.10, 0.70), "厌恶": (-0.70, 0.60), "嫌弃": (-0.50, 0.40),
    "无奈": (-0.30, 0.20), "疲惫": (-0.40, 0.10), "冷淡": (-0.30, 0.10),
    "疏离": (-0.30, 0.15), "不屑": (-0.40, 0.30), "心碎": (-0.90, 0.40),
    "背叛": (-0.85, 0.60), "刺痛": (-0.60, 0.50), "又气": (-0.50, 0.70),
    "心痛": (-0.70, 0.30), "心如刀绞": (-0.90, 0.60),
    "自毁": (-0.95, 0.30), "恨": (-0.90, 0.70),
}


def _lookup_vad(mood: str) -> tuple[float, float]:
    """子串匹配 VAD 表，返回 (valence, arousal)。零匹配时按极性回退。"""
    if not mood:
        return (0.0, 0.5)
    m = mood.strip()
    vals, arous = [], []
    for kw, (v, a) in _VAD_MAP.items():
        if kw in m:
            vals.append(v)
            arous.append(a)
    if vals:
        return (sum(vals) / len(vals), sum(arous) / len(arous))
    pol = _get_emotion_polarity(mood)
    if pol == 1:
        return (0.5, 0.5)
    if pol == -1:
        return (-0.5, 0.5)
    return (0.0, 0.5)


def _emotion_affinity(current_mood: str | None, memory_mood: str) -> float:
    """VAD 加权欧氏距离情感亲和度 [0,1]。

    valence 差权重为 arousal 的 2 倍（情感色调比激活度更重要）。
    """
    if current_mood is None:
        return 0.5
    cur_v, cur_a = _lookup_vad(current_mood)
    mem_v, mem_a = _lookup_vad(memory_mood)
    d_v = cur_v - mem_v      # [-2, 2]
    d_a = cur_a - mem_a      # [-1, 1]
    # 加权欧氏距离: valence 权重 2, arousal 权重 1
    dist = math.sqrt(0.5 * d_v * d_v + d_a * d_a)
    # 最大可能距离 sqrt(2*1 + 1*1) = sqrt(3)
    sim = 1.0 - dist / math.sqrt(3.0)
    return max(0.0, min(1.0, sim))


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
        """检索长期记忆，返回结构化 dict 列表（两级门控重排）。

        每条返回: {text, relevance, importance, age_seconds}
        两级门控: base = α·relevance_norm + β·recency + γ·importance_norm,
                  final = base × (1 + λ·emo_affinity)    # 乘性提亮,不翻转排序
        current_mood 为 None 时 emo_affinity=0.5（退化，向后兼容）。
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

        # 两级门控评分: base = α·rel + β·rec + γ·imp, final = base × (1 + λ·emo_affinity)
        for s in scored:
            relevance_norm = (s["relevance"] - rel_min) / rel_range
            age_hours = s["age_seconds"] / 3600.0
            recency = math.exp(-age_hours / RECENCY_TAU_HOURS)
            importance_norm = s["importance"] / 10.0
            emo_aff = _emotion_affinity(current_mood, s["memory_mood"])
            base = (
                RERANK_ALPHA * relevance_norm
                + RERANK_BETA * recency
                + RERANK_GAMMA * importance_norm
            )
            s["final"] = base * (1 + RERANK_LAMBDA * emo_aff)
            s["emo_affinity"] = emo_aff
            s["base"] = base

        scored.sort(key=lambda s: s["final"], reverse=True)
        top = scored[: self._search_top_k]
        if top:
            summary = ", ".join(
                f"imp={m['importance']} base={m['base']:.3f} emo_aff={m['emo_affinity']:.2f} final={m['final']:.3f}" for m in top[:3]
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

    def add_manual(self, text: str, card_id: str, metadata: dict | None = None) -> bool:
        """手动添加一条单文本记忆。infer=False 避免 Mem0 LLM 提炼丢弃。

        metadata 可选，用于标记反思记忆 is_reflection=True 等。
        """
        if not self.enabled:
            return False
        try:
            kwargs = {"user_id": card_id, "infer": False}
            if metadata:
                kwargs["metadata"] = metadata
            result = self._mem.add(text, **kwargs)
            print(f"[MemoryManager] manual add result: {result}")
            return True
        except Exception as exc:
            print(f"[MemoryManager] manual add failed: {exc}")
            return False

    def reflect(self, card_id: str, llm, recent_memories: list[dict], char_name: str) -> None:
        """把近期高重要性记忆综合成 1-2 条高阶洞察，后台写回。

        recent_memories: 已过滤的非反思记忆，每项含 text/importance/mood 等。
        llm: 复用 engine 的 LLM client（llm.chat(sp, [msg])）。
        """
        if not self.enabled:
            return
        if not recent_memories or llm is None:
            return

        def _do_reflect():
            try:
                mem_texts = "\n".join(
                    f"- [{m.get('importance', '?')}分] {m['text'][:200]}"
                    for m in recent_memories[:10]
                )
                prompt = (
                    f"你是{char_name}。请以第一人称回顾以下近期的重要对话记忆，"
                    f"提炼出 1-2 条关于「你和对方的关系变化」「你对对方的深层感受」"
                    f"或「你自己的成长」的高阶洞察。\n\n"
                    f"记忆列表：\n{mem_texts}\n\n"
                    f"要求：\n"
                    f"1. 每条洞察一句话，不要复述事实，要总结趋势或深层感悟\n"
                    f"2. 用{char_name}的第一人称口吻\n"
                    f"3. 只输出洞察本身，每条一行，不要编号、不要解释\n"
                    f"4. 如果记忆不足以形成洞察，输出空行"
                )
                reply = llm.chat(
                    "你是一个善于反思和内省的AI角色。",
                    [{"role": "user", "content": prompt}],
                )
                print(f"[Reflection] LLM reply ({len(reply)} chars): {reply[:300]}")

                insights = [
                    line.strip() for line in reply.split("\n")
                    if line.strip() and not line.strip().startswith("#")
                ]
                for insight in insights[:2]:
                    if len(insight) < 6:
                        continue
                    ok = self.add_manual(
                        insight, card_id,
                        metadata={"is_reflection": True, "importance": 8},
                    )
                    print(f"[Reflection] wrote insight (ok={ok}): {insight[:120]}")
            except Exception as exc:
                print(f"[Reflection] failed: {exc}")
                import traceback
                traceback.print_exc()

        threading.Thread(target=_do_reflect, daemon=True).start()

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
