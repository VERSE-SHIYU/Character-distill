"""基于 ChromaDB 与句向量模型的内存型 RAG 引擎。"""

from __future__ import annotations

import uuid
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.errors import NotFoundError

from core.embeddings import create_safe_embedding_fn
from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）


def _set_hits(sp, result) -> None:
    """检索 span 收尾：记命中条数（canonical 短键，导出归一到 app.retrieval.hits）。"""
    if sp is None or result is None:
        return
    T.set_attr(sp, "retrieval_hits", len(result))


class CollectionUnusableError(RuntimeError):
    """检索集合不可用：向量维度与当前 embedder 不符，或集合已损坏。

    与「真无匹配（空结果）」严格区分 —— 这是确定性、重试无用、静默无益的失败。
    曾根因：embedder 迁移前的 384 维旧集合被 load_existing 当"可用"装载（只看
    count()>0 不验维度），query 时 chroma 维度错又被两层宽 except 吞成 []，
    调用方当"没检索到"，场景检索静默失效、无日志。本异常让失败在上层可见。

    Attributes:
        collection_name: 出问题的集合名（查询路径未知时为 ""）。
        stored_dim: 集合内实际向量维度（load 时探测到才非 None）。
        expected_dim: 当前 embedder 期望维度（非 None 表示做了 load 时校验）。
    """

    def __init__(
        self,
        message: str,
        *,
        collection_name: str = "",
        stored_dim: int | None = None,
        expected_dim: int | None = None,
    ) -> None:
        super().__init__(message)
        self.collection_name = collection_name
        self.stored_dim = stored_dim
        self.expected_dim = expected_dim


# ── characters 元数据：写入格式与过滤逻辑的唯一出处 ─────────────────────
# chroma 1.5.9 实测（本机容器内，临时集合对照）：metadata 的 ``$contains`` 只对
# **数组**元数据生效，对字符串元数据恒不命中 —— 连 ``$contains`` 完整原串都是 0 命中
# （不带 where 3 条 / ``$contains`` 0 条 / 等值 3 条）。所以过滤不能在 chroma 侧做，
# 只能取回候选后在 Python 侧比对。写入格式仍是逗号分隔字符串，为的是不重建存量
# 集合（改数组元数据需全量重索引）。
CHARACTERS_NONE_TAG = "__none__"
CHARACTER_FILTER_MULTIPLIER = 4  # 候选倍数：先取 need×MULT 条候选再过滤
CHARACTER_FILTER_MAX_REFETCH = 1  # 过滤后仍不足时，扩大候选重取的次数上限


def characters_tag(names: list[str]) -> str:
    """characters 元数据的唯一写入格式：逗号分隔角色名，无角色时 ``__none__``。

    约束：角色名不能含逗号（否则与分隔符混淆）。写入方一律经此函数，不要在调用点
    各写一份格式 —— 两处不一致就是下一个坑。
    """
    return ",".join(names) if names else CHARACTERS_NONE_TAG


def filter_by_characters(
    metadatas: list[dict[str, Any] | None], character_name: str | None
) -> list[int]:
    """返回 metadatas 中 characters 命中 character_name 的下标。

    character_name 为空 → 不过滤（返回全部下标）。匹配是**子串匹配**：characters
    是逗号分隔的多角色串（如 ``"魏无羡,江澄"``），任一角色名命中即算命中 —— 这正是
    等值匹配（``$eq``）不可行的原因。
    """
    if not character_name:
        return list(range(len(metadatas)))
    return [
        i
        for i, meta in enumerate(metadatas)
        if character_name in str((meta or {}).get("characters", ""))
    ]


class SceneHits(list):
    """场景检索结果。是 ``list`` 子类，既有消费方（拼接片段 / 判空）无需改动。

    Attributes:
        candidates_exhausted: 扩大候选重取后，按 characters 过滤仍不足请求条数。
            为 True 表示「返回得比要的少，且已无候选可取」——调用方必须能看见，
            不能把少返回当成正常结果（本仓第五次同类缺陷：失败被吞成正常返回）。
    """

    def __init__(self, items: Any = (), *, candidates_exhausted: bool = False) -> None:
        super().__init__(items)
        self.candidates_exhausted = candidates_exhausted


class RAGEngine:
    """使用 ChromaDB 与阿里云百炼 text-embedding-v4 的 RAG 检索引擎。"""

    def __init__(self, config: dict[str, Any], chroma_path: str | None = None) -> None:
        """从配置字典初始化客户端、嵌入函数与集合占位字段。

        Note: ChromaDB 的 PersistenClient 默认向 ``./chroma`` 写入数据。
        为避免集合名冲突导致跨 session 的集合引用失效，每个引擎
        实例使用唯一的 UUID 作为集合名。

        Args:
            config: 需包含 chunk_size、chunk_overlap、top_k。
            chroma_path: chroma 持久化目录；缺省 ``./data/chroma_db``。测试可注入临时目录。
        """
        try:
            self._chunk_size: int = int(config["chunk_size"])
            self._chunk_overlap: int = int(config["chunk_overlap"])
            self._top_k: int = int(config["top_k"])
        except (KeyError, TypeError, ValueError) as exc:
            print(f"读取 RAG 配置字段失败：{exc}")
            raise

        try:
            self._client = chromadb.PersistentClient(path=chroma_path or "./data/chroma_db")
        except Exception as exc:
            print(f"初始化 Chroma EphemeralClient 失败：{exc}")
            raise

        try:
            self._embedding_function = create_safe_embedding_fn(
                api_key=config.get("embedding_key", ""),
                region=config.get("embedding_region", "cn"),
            )
        except Exception as exc:
            print(f"初始化 embedding 失败：{exc}")
            raise

        # 兑现上方 docstring 的承诺：每个引擎实例使用唯一 UUID 作为默认集合名，
        # 避免调用方未显式传 collection_name 时落到空串导致 ChromaDB 校验失败
        # (collection 名要求 3-512 字符)。
        self._collection_name: str = f"rag_{uuid.uuid4().hex}"
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

    def _tag_characters(self, chunk_text: str, all_characters: list[dict[str, Any]]) -> str:
        """检出本 chunk 出场的角色，按 characters_tag() 的格式打成元数据值。

        格式的唯一出处是 characters_tag()；过滤不走 chroma（``$contains`` 对字符串
        元数据恒不命中，见模块顶部实测），而是读回后在 filter_by_characters() 里比对。
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
        return characters_tag(sorted(found))

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
                {"characters": self._tag_characters(chunk, all_characters)}
                for chunk in filtered
            ]
        try:
            collection.add(**add_kwargs)
            print(f"[embed-stats] RAG index text chunks={len(filtered)} collection={name}")
        except Exception as exc:
            print(f"向 Chroma collection 写入文档失败：{exc}")
            raise

        self.collection = collection
        self.collection_name = name

    @T.spanned("rag.query", finalize=lambda sp, self, res, exc: _set_hits(sp, res))
    def query(
        self, query_text: str, character_name: str | None = None, top_k: int | None = None
    ) -> SceneHits:
        """对当前集合执行相似度检索，可按角色名过滤。

        Args:
            query_text: 查询语句。
            character_name: 可选角色名，传入后仅返回该角色出场的片段。过滤在 Python
                侧做（chroma 的 ``$contains`` 对字符串元数据不命中，见模块顶部实测）。
            top_k: 返回片段数，默认使用配置值 ``self._top_k``。

        Returns:
            SceneHits；未索引时返回空。过滤后取不满时 ``candidates_exhausted`` 为
            True（日志同时如实报告），不静默少返回。
        """
        if self.collection is None:
            return SceneHits()

        docs, _, _, exhausted = self._fetch_candidates(
            query_text,
            character_name,
            top_k or self._top_k,
            ["documents", "metadatas"],
        )
        return SceneHits(docs, candidates_exhausted=exhausted)

    def _fetch_candidates(
        self,
        query_text: str,
        character_name: str | None,
        need: int,
        include: list[str],
    ) -> tuple[list[str], list, list[dict[str, Any] | None], bool]:
        """取候选并按 characters 过滤，过滤后不足则扩大候选重取。

        Returns:
            ``(docs, distances, metadatas, candidates_exhausted)``，三者均已按角色
            过滤过。返回条数可能少于 need；此时 candidates_exhausted 为 True 且
            （有角色过滤时）日志如实报告 —— 绝不静默少返回。
        """
        want = max(need, 1)
        # 有角色过滤才要超取：无过滤时多取没有意义，徒增候选。
        n = want * CHARACTER_FILTER_MULTIPLIER if character_name else want
        refetches = 0
        while True:
            try:
                results = self.collection.query(
                    query_texts=[query_text], n_results=n, include=include
                )
            except Exception as exc:
                # 不再吞成空：查询失败（含维度不符/损坏）与"真无匹配"必须可区分。
                # 真无匹配时 chroma 返回空 documents 不抛异常；凡是抛出的都是真失败，
                # 显式上抛，由 ContextEngine._retrieve_scenes 降级并记日志。
                raise CollectionUnusableError(f"向量检索查询失败：{exc}") from exc

            docs = list((results.get("documents") or [[]])[0] or [])
            dists = list((results.get("distances") or [[]])[0] or [])
            metas = list((results.get("metadatas") or [[]])[0] or [])
            keep = filter_by_characters(metas, character_name)
            if len(keep) >= want:
                exhausted = False
            elif refetches >= CHARACTER_FILTER_MAX_REFETCH or len(docs) < n:
                # 取回条数少于请求数 → 库内候选已取尽，再重取也不会有新的。
                exhausted = True
            else:
                n *= CHARACTER_FILTER_MULTIPLIER  # ponytail: 线性倍增，集合够大时会多取几次；够用且可读
                refetches += 1
                continue

            if exhausted and character_name:
                print(
                    f"[RAGEngine] characters 过滤后候选耗尽：need={want} got={len(keep)} "
                    f"（候选 {len(docs)}/{n}，重取 {refetches} 次）"
                )

            def _take(seq: list) -> list:
                return [seq[i] for i in keep] if len(seq) == len(docs) else []

            return _take(docs), _take(dists), _take(metas), exhausted

    @T.spanned("rag.query_emotion", finalize=lambda sp, self, res, exc: _set_hits(sp, res))
    def query_with_emotion(
        self,
        query_text: str,
        current_emotion: str = "平静",
        character_name: str | None = None,
        top_k: int = 3,
    ) -> list[str]:
        """情感加权检索：语义相似度 0.7 + 情感匹配 0.3。

        Args:
            query_text: 用户消息。
            current_emotion: 当前对话情感（由调用方判断）。
            character_name: 按角色过滤。
            top_k: 最终返回数量。

        Returns:
            按 final_score 排序的 SceneHits。过滤后取不满时 ``candidates_exhausted``
            为 True（日志同时如实报告），不静默少返回。
        """
        if self.collection is None:
            return SceneHits()

        docs, dists, metas, exhausted = self._fetch_candidates(
            query_text,
            character_name,
            top_k,
            ["documents", "distances", "metadatas"],
        )

        if not docs:
            return SceneHits(candidates_exhausted=exhausted)

        _EMO_DISTANCE: dict[tuple[str, str], float] = {
            ("悲伤", "悲伤"): 1.0, ("愤怒", "愤怒"): 1.0,
            ("温柔", "温柔"): 1.0, ("紧张", "紧张"): 1.0,
            ("委屈", "委屈"): 1.0, ("平静", "平静"): 1.0,
            ("悲伤", "委屈"): 0.7, ("委屈", "悲伤"): 0.7,
            ("愤怒", "委屈"): 0.5, ("委屈", "愤怒"): 0.5,
            ("愤怒", "紧张"): 0.6, ("紧张", "愤怒"): 0.6,
            ("温柔", "平静"): 0.5, ("平静", "温柔"): 0.5,
            ("悲伤", "温柔"): 0.4, ("温柔", "悲伤"): 0.4,
            ("紧张", "委屈"): 0.4, ("委屈", "紧张"): 0.4,
            ("愤怒", "温柔"): 0.1, ("温柔", "愤怒"): 0.1,
            ("愤怒", "悲伤"): 0.3, ("悲伤", "愤怒"): 0.3,
            ("愤怒", "平静"): 0.2, ("平静", "愤怒"): 0.2,
            ("悲伤", "平静"): 0.3, ("平静", "悲伤"): 0.3,
            ("紧张", "平静"): 0.3, ("平静", "紧张"): 0.3,
            ("紧张", "温柔"): 0.3, ("温柔", "紧张"): 0.3,
            ("委屈", "温柔"): 0.4, ("温柔", "委屈"): 0.4,
            ("委屈", "平静"): 0.3, ("平静", "委屈"): 0.3,
            ("紧张", "悲伤"): 0.4, ("悲伤", "紧张"): 0.4,
        }

        def emo_sim(e1: str, e2: str) -> float:
            return _EMO_DISTANCE.get((e1, e2)) or _EMO_DISTANCE.get((e2, e1)) or 0.2

        max_dist = max(dists) if dists else 1.0
        max_dist = max(max_dist, 1e-6)
        scored = []
        for doc, dist, meta in zip(docs, dists, metas):
            semantic = 1.0 - dist / max_dist
            emotion = (meta or {}).get("emotion", "平静")
            final = 0.7 * semantic + 0.3 * emo_sim(current_emotion, emotion)
            scored.append((final, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return SceneHits([doc for _, doc in scored[:top_k]], candidates_exhausted=exhausted)

    @staticmethod
    def _peek_dimension(col: Collection) -> int | None:
        """读集合内首条向量的维度（本地 op，不触发 embedding 网络请求）。

        空集合 / peek 失败 → None（无法校验；查询层对失败显式上抛兜底）。
        peek 返回的 embeddings 可能是 list 或 numpy 数组，避免对数组做真值判断。
        """
        try:
            result = col.peek(limit=1)
        except Exception:
            return None
        embs = result.get("embeddings") if result is not None else None
        if embs is None or len(embs) == 0:
            return None
        try:
            return int(len(embs[0]))
        except (TypeError, ValueError):
            return None

    def load_existing(self, collection_name: str) -> bool:
        """装载已存在的持久化集合，可用返回 True。

        集合不存在 / 空 → False（调用方视为"无数据"，自行决定是否重建）。
        get_collection 真错误 → 记日志后返回 False（不再静默 pass）。
        集合向量维度与当前 embedder 不符（如 embedder 迁移前的旧集合）→ 抛
        CollectionUnusableError：确定性不可用，显式让上层感知 —— 绝不返回 True
        制造"已装载但查询恒空"的静默失效，也不与"无集合"的 False 混淆而盲目重建。
        """
        try:
            col = self._client.get_collection(
                name=collection_name,
                embedding_function=self._embedding_function,
            )
        except NotFoundError:
            return False
        except Exception as exc:
            print(f"[RAGEngine] load_existing 获取集合失败（{collection_name}）：{exc}")
            return False
        if col.count() == 0:
            return False

        expected = getattr(self._embedding_function, "_dimensions", None)
        if expected is not None:
            stored = self._peek_dimension(col)
            if stored is not None and stored != expected:
                raise CollectionUnusableError(
                    f"集合 {collection_name} 向量维度 {stored} 与当前 embedder 期望 "
                    f"{expected} 不符：该集合由其他 embedder（如迁移前 384 维）写入，"
                    f"需按当前 embedder 重建后才可查询。",
                    collection_name=collection_name,
                    stored_dim=stored,
                    expected_dim=expected,
                )
        self.collection = col
        self.collection_name = collection_name
        return True

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
