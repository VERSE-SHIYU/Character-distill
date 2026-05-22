"""场景索引器 — 把原文切成带 metadata 的场景，存入 ChromaDB。

当前版本：基于关键词规则切分场景（不依赖 LLM API，零成本）。
未来版本：调用 LLM 做语义场景边界识别 + 情感标注。
"""
from __future__ import annotations

import re
from typing import Any

from core.rag import RAGEngine

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
    ) -> int:
        """切分场景并写入 RAG，返回场景数量。

        注意：此方法会替换 rag.collection 引用，将检索从 chunk 模式
        升级为 scene 模式。这是设计意图——蒸馏完成后，对话阶段应使用
        场景级检索以获得更好的上下文连贯性。
        """
        scenes = self._split_scenes(text)
        if not scenes:
            return 0

        name = collection_name or f"scenes_{character_name}"
        try:
            rag._client.delete_collection(name=name)
        except Exception:
            pass

        collection = rag._client.create_collection(
            name=name,
            embedding_function=rag._embedding_function,
        )

        ids, docs, metas = [], [], []
        for i, scene in enumerate(scenes):
            emotion = _detect_emotion(scene)
            ids.append(f"scene_{i}")
            docs.append(scene[:800])
            metas.append({
                "emotion": emotion,
                "characters": character_name,
                "scene_index": str(i),
            })

        collection.add(documents=docs, ids=ids, metadatas=metas)

        rag.collection = collection
        rag.collection_name = name

        return len(scenes)

    def _split_scenes(self, text: str) -> list[str]:
        """按场景边界切分，每段保持 200-1000 字。"""
        if re.search(r'^\[\d{4}-\d{2}-\d{2}\]', text, re.MULTILINE):
            return self._split_chat_scenes(text)

        parts = self.SCENE_BREAKS.split(text)
        scenes: list[str] = []
        for p in parts:
            p = p.strip()
            if len(p) < 50:
                continue
            if len(p) > 1000:
                sub = [s.strip() for s in p.split("\n\n") if len(s.strip()) > 50]
                scenes.extend(sub)
            else:
                scenes.append(p)
        return scenes

    def _split_chat_scenes(self, text: str) -> list[str]:
        """按日期分组，每天一个场景。"""
        lines = text.strip().split('\n')
        scenes: list[str] = []
        current_day = None
        current_lines: list[str] = []

        date_re = re.compile(r'^\[(\d{4}-\d{2}-\d{2})\]')
        for line in lines:
            m = date_re.match(line)
            day = m.group(1) if m else current_day
            if day != current_day and current_lines:
                scene = '\n'.join(current_lines)
                if len(scene.strip()) > 50:
                    scenes.append(scene)
                current_lines = []
            current_day = day
            current_lines.append(line)

        if current_lines:
            scene = '\n'.join(current_lines)
            if len(scene.strip()) > 50:
                scenes.append(scene)

        return scenes
