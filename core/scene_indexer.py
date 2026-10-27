"""场景索引器 — 把原文切成带 metadata 的场景，存入 ChromaDB。

当前版本：基于关键词规则切分场景（不依赖 LLM API，零成本）。
未来版本：调用 LLM 做语义场景边界识别 + 情感标注。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from chromadb.errors import NotFoundError

from core.fingerprint import content_fingerprint
from core.quotes import leading_ws, normalized_starts
from core.rag import (
    FINGERPRINT_KEY, POS_SCHEMA, POS_SCHEMA_KEY,
    CollectionUnusableError, RAGEngine, characters_tag, mark_built,
)

# 简单情感关键词映射（可扩充）
_EMOTION_KEYWORDS: dict[str, list[str]] = {
    "悲伤": ["哭", "泪", "痛", "绝望", "失去", "离开", "死", "心碎", "委屈", "难过", "心疼", "再见", "遗憾", "孤独"],
    "愤怒": ["愤", "怒", "骂", "恨", "滚", "混蛋", "不可原谅", "凭什么", "够了", "闭嘴", "讨厌", "受够"],
    "温柔": ["温柔", "轻声", "微笑", "牵手", "抱", "安慰", "陪着", "没关系", "乖", "别怕", "在呢", "心疼你"],
    "紧张": ["心跳", "颤抖", "屏住呼吸", "慌", "紧张", "害怕", "不敢", "怎么办", "糟了", "完了"],
    "委屈": ["为什么不理", "算了", "随便", "不想说", "无所谓", "你不在乎", "是我的错", "对不起打扰了", "我走"],
    "平静": [],
}


def _detect_emotion(text: str) -> str:
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return emotion
    return "平静"


# ── 切法（召回 r1，docs/specs/recall-r1-chunking.md §4）──────────────────────

@dataclass(frozen=True)
class ChunkScheme:
    """场景切法。``para``：单换行分段，连续整段攒到 ``chars`` 字，单段超预算只在句末切；
    ``fixed``：固定 ``chars`` 字、相邻块重叠 ``overlap`` 字。"""

    mode: Literal["para", "fixed"]
    chars: int
    overlap: int = 0

    def __post_init__(self) -> None:
        if self.mode not in ("para", "fixed") or self.chars <= 0 or not 0 <= self.overlap < self.chars:
            raise ValueError(f"非法切法：{self}")

    def key(self) -> str:
        """写进集合 metadata 的稳定字符串 —— 复用判断只认它。"""
        return f"{self.mode}:{self.chars}:{self.overlap}"


# 占位：S4 按实测 R@3 换成胜出变体（A/B/C），其余变体不留路径。字数预算待 S3 量出
# token/字比例后定（spec §3），此值未核实。
CHUNK_SCHEME = ChunkScheme(mode="para", chars=300, overlap=0)

# 集合 metadata 里记切法的键名；切法不同即重建（存卡、开卡两条路径都比）。
_CHUNK_SCHEME_KEY = "chunk_scheme"

# 句末：。！？… 及其后紧跟的右引号 / 右括号（spec §1 第 2 条）。
_SENTENCE = re.compile(r"[^。！？…]*[。！？…]+[”’」』）)]*|[^。！？…]+$")
_PARAGRAPH = re.compile(r"[^\n]+")


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    """`[start, end)` 去掉首尾空白后的区间；全是空白返回 None。"""
    piece = text[start:end]
    if not piece.strip():
        return None
    return start + leading_ws(piece), end - (len(piece) - len(piece.rstrip()))


def _pack(text: str, units: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    """把相邻单元攒成块：加上下一个单元超预算就收口；单个单元超预算独自成块。"""
    out: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    for s, e in units:
        if cur is not None and e - cur[0] <= budget:
            cur = (cur[0], e)
        else:
            if cur is not None:
                out.append(cur)
            cur = (s, e)
    if cur is not None:
        out.append(cur)
    return out


def _chunk_paragraphs(text: str, budget: int) -> list[tuple[int, int]]:
    """连续整段攒到预算；单段超预算按句末切开再攒（不与别段混块）。"""
    blocks: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []          # 正在攒的整段
    for m in _PARAGRAPH.finditer(text):
        span = _trim(text, m.start(), m.end())
        if span is None:
            continue
        if span[1] - span[0] <= budget:
            run.append(span)
            continue
        blocks += _pack(text, run, budget)
        run = []
        sentences = [t for sm in _SENTENCE.finditer(text, span[0], span[1])
                     if (t := _trim(text, sm.start(), sm.end())) is not None]
        blocks += _pack(text, sentences, budget)
    return blocks + _pack(text, run, budget)


def _chunk_fixed(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """固定长度窗口，步长 ``size - overlap``，最后一块到全文末尾。"""
    out: list[tuple[int, int]] = []
    start, step = 0, size - overlap
    while True:
        end = min(start + size, len(text))
        out.append((start, end))
        if end == len(text):
            return out
        start += step


# 集合 metadata 里存场景幂等键的那个键名（含它的集合即「本正文已建好」）。
_FINGERPRINT_KEY = FINGERPRINT_KEY
# 位置坐标的格局标记：集合元数据带它，条目带 `npos`（规范化坐标）。
_POS_SCHEMA_KEY = POS_SCHEMA_KEY
_POS_SCHEMA_VERSION = POS_SCHEMA


class SceneIndexer:
    """将原文按场景切分，存入 RAGEngine 的 ChromaDB，携带情感 metadata。"""

    def index_scenes(
        self,
        text: str,
        rag: RAGEngine,
        character_name: str,
        collection_name: str | None = None,
        need_positions: bool = False,
        scheme: ChunkScheme | None = None,
    ) -> int:
        """切分场景并写入 RAG，返回场景数量。**幂等**：正文没变即复用已建好的集合。

        注意：此方法会把 ``rag.collection`` 改指到新建 / 复用的场景集合。传进来的
        ``rag`` 必须是调用方**私有**的引擎（`IndexingService.build_scene_index` 里那一个）
        —— 不许是某个会话正在用的引擎。会话各自经 `IndexingService.get_rag_for_session`
        拿 `SessionRag`，按 `scenes_{card_id}` → `text_{text_id}` 装载。

        幂等的由来：本方法有**两处**调度者（蒸馏落卡、打开卡片时的
        `/start_session`），两者之间隔着人操作时间（分钟到天），
        `IndexingService` 那层去重只管同一时刻（跑着时再来的排在后面），
        挡不住隔了很久的第二次。而重建要付两笔代价：一次全量 embedding，以及
        `delete_collection` 先于 `create_collection` 的那段空窗 —— 这期间会话
        检索跳过本集合（回落原文集合，或本轮记为失败）。故先按正文指纹复用；
        正文变了（重解析）或维度不符才重建。

        ``need_positions`` 只在**带起点的卡存卡**时由 `save_distilled_card` 传 True：
        指纹相同但集合没有 `pos_schema`（这条 feature 之前建的）时也重建一次，把
        `npos` 补上（覆盖重蒸同名卡沿用 card_id 的情况）。不带该标志的既有调度
        （打开卡片等）不比 `pos_schema`。

        **切法**（``scheme``，缺省 `CHUNK_SCHEME`）记在集合 metadata 的 `chunk_scheme`；
        两条路径都比它，不一致即重建 —— 换切法不靠升 `POS_SCHEMA`（检索窗口依赖那个值）。
        每块存整块原文，条目带 `npos` / `nend`（规范化坐标，一次算出）。
        """
        scheme = scheme or CHUNK_SCHEME
        scenes = self._split_scenes(text, scheme)
        if not scenes:
            return 0

        name = collection_name or f"scenes_{character_name}"
        fingerprint = content_fingerprint(text)

        try:
            if rag.load_existing(name):
                meta = rag.collection.metadata or {}
                if (meta.get(_FINGERPRINT_KEY) == fingerprint
                        and meta.get(_CHUNK_SCHEME_KEY) == scheme.key()
                        and not (need_positions
                                 and meta.get(_POS_SCHEMA_KEY) != _POS_SCHEMA_VERSION)):
                    return rag.collection.count()
        except CollectionUnusableError:
            # 维度与当前 embedder 不符（换过 embedding 配置）：旧集合查询恒失败，
            # 不是「已建好」，落到下面按当前 embedder 重建。
            pass

        try:
            rag._client.delete_collection(name=name)
        except NotFoundError:
            # 同 core/rag.py::index：集合还不存在是唯一预期失败，其余真故障照旧上抛
            pass

        collection = rag._client.create_collection(
            name=name,
            embedding_function=rag._embedding_function,
            metadata={_FINGERPRINT_KEY: fingerprint, _POS_SCHEMA_KEY: _POS_SCHEMA_VERSION,
                      _CHUNK_SCHEME_KEY: scheme.key()},
        )

        coords = normalized_starts(text, [s for s, _, _ in scenes] + [e for _, e, _ in scenes])
        npos, nend = coords[:len(scenes)], coords[len(scenes):]
        ids, docs, metas = [], [], []
        for i, (_, _, scene) in enumerate(scenes):
            emotion = _detect_emotion(scene)
            ids.append(f"scene_{i}")
            docs.append(scene)
            metas.append({
                "emotion": emotion,
                # 格式唯一出处：core.rag.characters_tag（与 text_* 集合写入一致）
                "characters": characters_tag([character_name]),
                "scene_index": str(i),
                "npos": npos[i],
                "nend": nend[i],
            })

        collection.add(documents=docs, ids=ids, metadatas=metas)
        mark_built(collection)
        print(f"[embed-stats] Scene index scenes={len(docs)} collection={name}")

        rag.collection = collection
        rag.collection_name = name

        return len(scenes)

    def _split_scenes(self, text: str, scheme: ChunkScheme) -> list[tuple[int, int, str]]:
        """按 ``scheme`` 切块，返回 `(原文起点, 原文终点, text[起点:终点])`。

        坐标在切分时就知道（按下标切），**不回找** —— `text.find` 对重复段落会取到第一处，
        起点就错了（C25）。聊天记录仍按日期分组（`_split_chat_scenes`，不受切法影响）。
        """
        if re.search(r'^\[\d{4}-\d{2}-\d{2}\]', text, re.MULTILINE):
            return [(s, s + len(scene), scene) for s, scene in self._split_chat_scenes(text)]
        if scheme.mode == "fixed":
            spans = _chunk_fixed(text, scheme.chars, scheme.overlap)
        else:
            spans = _chunk_paragraphs(text, scheme.chars)
        return [(s, e, text[s:e]) for s, e in spans]

    def _split_chat_scenes(self, text: str) -> list[tuple[int, str]]:
        """按日期分组，每天一个场景，返回 `(原文起点, 片段)`。"""
        date_re = re.compile(r'^\[(\d{4}-\d{2}-\d{2})\]')
        stripped = text.strip()
        lead = len(text) - len(text.lstrip())
        lines = stripped.split('\n')
        starts: list[int] = []
        pos = lead
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1

        scenes: list[tuple[int, str]] = []
        current_day = None
        current_lines: list[str] = []
        current_start = 0
        for idx, line in enumerate(lines):
            m = date_re.match(line)
            day = m.group(1) if m else current_day
            if day != current_day and current_lines:
                scene = '\n'.join(current_lines)
                if len(scene.strip()) > 50:
                    scenes.append((current_start, scene))
                current_lines = []
            if not current_lines:
                current_start = starts[idx]
            current_day = day
            current_lines.append(line)

        if current_lines:
            scene = '\n'.join(current_lines)
            if len(scene.strip()) > 50:
                scenes.append((current_start, scene))

        return scenes
