"""基于 ChromaDB 与句向量模型的内存型 RAG 引擎。"""

from __future__ import annotations

import uuid
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

from core.embeddings import create_safe_embedding_fn


class RAGEngine:
    """使用 Ephemeral Chroma 客户端与 SentenceTransformer 嵌入的检索引擎。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """从配置字典初始化客户端、嵌入函数与集合占位字段。

        Note: ChromaDB 的 EphemeralClient 并非纯内存实现——它默认向
        ``./chroma`` 写入数据且多实例间共享同一持久化目录。
        为避免集合名冲突导致跨 session 的集合引用失效，每个引擎
        实例使用唯一的 UUID 作为集合名。

        Args:
            config: 需包含 chunk_size、chunk_overlap、top_k、embedding_model。
        """
        try:
            self._chunk_size: int = int(config["chunk_size"])
            self._chunk_overlap: int = int(config["chunk_overlap"])
            self._top_k: int = int(config["top_k"])
            self._embedding_model: str = str(config["embedding_model"])
        except (KeyError, TypeError, ValueError) as exc:
            print(f"读取 RAG 配置字段失败：{exc}")
            raise

        try:
            self._client = chromadb.EphemeralClient()
        except Exception as exc:
            print(f"初始化 Chroma EphemeralClient 失败：{exc}")
            raise

        try:
            self._embedding_function = create_safe_embedding_fn(self._embedding_model)
        except Exception as exc:
            print(f"初始化 SentenceTransformer embedding 失败：{exc}")
            raise

        # Use a unique collection name per engine instance so that multiple
        # RAGEngines (each created for a different session) do not overwrite
        # each other's collections in the shared ChromaDB persist directory.
        self._collection_name: str = uuid.uuid4().hex[:16]
        self.collection: Collection | None = None
        self.collection_name: str | None = None

    def _chunk_text(self, text: str) -> list[str]:
        """按字符长度切片，优先在句号或换行处断开，并应用重叠窗口。

        Args:
            text: 原始文本。

        Returns:
            非空文本片段列表。
        """
        if not text:
            return []

        chunks: list[str] = []
        start = 0
        n = len(text)

        while start < n:
            hard_end = min(start + self._chunk_size, n)
            end = hard_end

            if hard_end < n:
                window = text[start:hard_end]
                break_rel: int | None = None
                for i in range(len(window) - 1, -1, -1):
                    ch = window[i]
                    if ch in "\n\r":
                        break_rel = start + i + 1
                        break
                    if ch == "。":
                        break_rel = start + i + 1
                        break
                if break_rel is not None and break_rel > start:
                    end = break_rel

            segment = text[start:end].strip()
            if segment:
                chunks.append(segment)

            if end >= n:
                break

            step = max(1, self._chunk_size - self._chunk_overlap)
            next_start = end - self._chunk_overlap
            if next_start <= start:
                next_start = start + step
            start = next_start

        return chunks

    def _tag_characters(self, chunk_text: str, all_characters: list[dict[str, Any]]) -> list[str]:
        """标注 chunk 中出场角色，返回主名列表。

        Args:
            chunk_text: 文本片段。
            all_characters: 角色信息字典列表，每项含 name 和 aliases。

        Returns:
            角色主名字符串列表（去重排序）。ChromaDB 的 ``$contains`` 对列表做元素匹配。
        """
        found: set[str] = set()
        lower_text = chunk_text.lower()
        for char in all_characters:
            name = char.get("name", "")
            if not name:
                continue
            terms = [name] + char.get("aliases", [])
            for term in terms:
                if term.lower() in lower_text:
                    found.add(name)
                    break
        return sorted(found)

    def index(
        self,
        text: str,
        collection_name: str | None = None,
        all_characters: list[dict[str, Any]] | None = None,
    ) -> None:
        """重建同名集合并写入切片后的文档。

        Args:
            text: 待索引正文。
            collection_name: 集合名称；未指定时使用实例唯一的 UUID。
        """
        name = collection_name or self._collection_name
        try:
            self._client.delete_collection(name=name)
        except Exception:
            pass

        try:
            collection = self._client.create_collection(
                name=name,
                embedding_function=self._embedding_function,
            )
        except Exception as exc:
            print(f"创建 Chroma collection 失败：{exc}")
            raise

        fragments = self._chunk_text(text)
        filtered = [piece for piece in fragments if piece.strip()]
        if not filtered:
            print("警告：切片后没有可用的非空文本片段，跳过写入向量库")
            self.collection = collection
            self.collection_name = name
            return

        ids = [f"chunk_{i}" for i in range(len(filtered))]
        add_kwargs: dict[str, Any] = {"documents": filtered, "ids": ids}
        if all_characters:
            add_kwargs["metadatas"] = [
                {"characters": self._tag_characters(chunk, all_characters) or ["__none__"]}
                for chunk in filtered
            ]
        try:
            collection.add(**add_kwargs)
        except Exception as exc:
            print(f"向 Chroma collection 写入文档失败：{exc}")
            raise

        self.collection = collection
        self.collection_name = name

    def query(
        self, query_text: str, character_name: str | None = None, top_k: int | None = None
    ) -> list[str]:
        """对当前集合执行相似度检索，可按角色名过滤。

        Args:
            query_text: 查询语句。
            character_name: 可选角色名，传入后仅返回该角色出场的片段。
            top_k: 返回片段数，默认使用配置值 ``self._top_k``。

        Returns:
            命中片段文本列表；未索引时返回空列表。
        """
        if self.collection is None:
            return []

        where = (
            {"characters": {"$contains": character_name}}
            if character_name
            else None
        )
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=top_k or self._top_k,
                where=where,
            )
        except Exception as exc:
            print(f"向量检索查询失败（降级返回空）：{exc}")
            return []

        documents = results.get("documents")
        if not documents:
            print("警告：检索结果缺少 documents 字段")
            return []
        first = documents[0]
        if first is None:
            return []
        return list(first)

    def reset(self) -> None:
        """删除当前集合并清空内存引用。"""
        name = self.collection_name
        if name:
            try:
                self._client.delete_collection(name=name)
            except Exception as exc:
                print(f"删除 Chroma collection 失败：{exc}")

        self.collection = None
        self.collection_name = None
