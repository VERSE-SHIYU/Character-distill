"""基于 ChromaDB 与句向量模型的内存型 RAG 引擎。"""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils import embedding_functions


class RAGEngine:
    """使用 Ephemeral Chroma 客户端与 SentenceTransformer 嵌入的检索引擎。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """从配置字典初始化客户端、嵌入函数与集合占位字段。

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
            self._embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self._embedding_model,
            )
        except Exception as exc:
            print(f"初始化 SentenceTransformerEmbeddingFunction 失败：{exc}")
            raise

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

    def index(self, text: str, collection_name: str = "default") -> None:
        """重建同名集合并写入切片后的文档。

        Args:
            text: 待索引正文。
            collection_name: 集合名称。
        """
        try:
            self._client.delete_collection(name=collection_name)
        except Exception:
            pass

        try:
            collection = self._client.create_collection(
                name=collection_name,
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
            self.collection_name = collection_name
            return

        ids = [f"chunk_{i}" for i in range(len(filtered))]
        try:
            collection.add(documents=filtered, ids=ids)
        except Exception as exc:
            print(f"向 Chroma collection 写入文档失败：{exc}")
            raise

        self.collection = collection
        self.collection_name = collection_name

    def query(self, query_text: str) -> list[str]:
        """对当前集合执行相似度检索。

        Args:
            query_text: 查询语句。

        Returns:
            命中片段文本列表；未索引时返回空列表。
        """
        if self.collection is None:
            return []

        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=self._top_k,
            )
        except Exception as exc:
            print(f"向量检索查询失败：{exc}")
            raise

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
