"""场景索引器 — 把原文切成带 metadata 的场景，存入 ChromaDB。

当前版本：基于关键词规则切分场景（不依赖 LLM API，零成本）。
未来版本：调用 LLM 做语义场景边界识别 + 情感标注。
"""
from __future__ import annotations

import re

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


def _span_texts(pattern, text: str, base: int = 0) -> list[tuple[int, int]]:
    """`text` 被 `pattern` 分隔后各段的半开区间 `[起, 止)`，相对 `base` 偏移。

    分隔符本身不计入 —— 与 `re.split` 丢分隔符同形，但保留下标（切分坐标的来源）。
    """
    out: list[tuple[int, int]] = []
    pos = 0
    for m in pattern.finditer(text):
        out.append((base + pos, base + m.start()))
        pos = m.end()
    out.append((base + pos, base + len(text)))
    return out




# 集合 metadata 里存场景幂等键的那个键名（含它的集合即「本正文已建好」）。
_FINGERPRINT_KEY = FINGERPRINT_KEY
# 位置坐标的格局标记：集合元数据带它，条目带 `npos`（规范化坐标）。
_POS_SCHEMA_KEY = POS_SCHEMA_KEY
_POS_SCHEMA_VERSION = POS_SCHEMA


class SceneIndexer:
    """将原文按场景切分，存入 RAGEngine 的 ChromaDB，携带情感 metadata。"""

    # 场景边界触发词（时间/地点/视角转换）
    SCENE_BREAKS = re.compile(
        r"(?:第[一二三四五六七八九十百千\d]+[章节回]|"
        r"\n{2,}|"
        r"(?:次日|翌日|傍晚|深夜|清晨|午后|几天后|多年后))"
    )

    def index_scenes(
        self,
        text: str,
        rag: RAGEngine,
        character_name: str,
        collection_name: str | None = None,
        need_positions: bool = False,
    ) -> int:
        """切分场景并写入 RAG，返回场景数量。**幂等**：正文没变即复用已建好的集合。

        注意：此方法会把 ``rag.collection`` 改指到新建 / 复用的场景集合。传进来的
        ``rag`` 必须是调用方**私有**的引擎（`IndexingService._scene_index_job` 里那一个）
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
        （打开卡片等）幂等规则不变 —— 旧卡不会被重新嵌入。
        """
        scenes = self._split_scenes(text)
        if not scenes:
            return 0

        name = collection_name or f"scenes_{character_name}"
        fingerprint = content_fingerprint(text)

        try:
            if rag.load_existing(name):
                meta = rag.collection.metadata or {}
                if meta.get(_FINGERPRINT_KEY) == fingerprint:
                    if not (need_positions and meta.get(_POS_SCHEMA_KEY) != _POS_SCHEMA_VERSION):
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
            metadata={_FINGERPRINT_KEY: fingerprint, _POS_SCHEMA_KEY: _POS_SCHEMA_VERSION},
        )

        npos = normalized_starts(text, [start for start, _ in scenes])
        ids, docs, metas = [], [], []
        for i, (start, scene) in enumerate(scenes):
            emotion = _detect_emotion(scene)
            ids.append(f"scene_{i}")
            docs.append(scene[:800])
            metas.append({
                "emotion": emotion,
                # 格式唯一出处：core.rag.characters_tag（与 text_* 集合写入一致）
                "characters": characters_tag([character_name]),
                "scene_index": str(i),
                "npos": npos[i],
            })

        collection.add(documents=docs, ids=ids, metadatas=metas)
        mark_built(collection)
        print(f"[embed-stats] Scene index scenes={len(docs)} collection={name}")

        rag.collection = collection
        rag.collection_name = name

        return len(scenes)

    def _split_scenes(self, text: str) -> list[tuple[int, str]]:
        """按场景边界切分，返回 `(原文起点, 片段)`，每段 200-1000 字。

        起点在切分时就知道（切分本来就是按下标切），**不回找** —— `text.find` 对重复
        段落会取到第一处，起点就错了（C25）。
        """
        if re.search(r'^\[\d{4}-\d{2}-\d{2}\]', text, re.MULTILINE):
            return self._split_chat_scenes(text)

        scenes: list[tuple[int, str]] = []
        for s, e in _span_texts(self.SCENE_BREAKS, text):
            p = text[s:e].strip()
            if len(p) < 50:
                continue
            if len(p) > 1000:
                for a, b in _span_texts(re.compile(r"\n\n"), text[s:e], base=s):
                    seg = text[a:b].strip()
                    if len(seg) > 50:
                        scenes.append((a + leading_ws(text[a:b]), seg))
            else:
                scenes.append((s + leading_ws(text[s:e]), p))
        return scenes

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
