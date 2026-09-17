# -*- coding: utf-8 -*-
"""锁：场景预索引的调度闭环 —— ① 每条落卡路都带 `indexing_service`，② 重复调度无害。

**它防的是什么（这一条红线写在最前）。** 这两半必须同一次做完，
**只做一半比不做更坏**：

  - **只做 ①**（给 `/start` 的 `_save_card` 补齐调度，不碰幂等）：它把
    「静默不索引」换成「**静默双倍索引 + `delete_collection` 删掉此刻在服务的集合**」。
    `index_scenes` 原先无条件 `delete_collection` → `create_collection`，中间那段
    **空窗**里正在读这个集合的会话查询恒空；而 `rag.collection` 是**共享的**
    （`IndexingService` 把同一个 `RAGEngine` 交给后续每一轮对话）。删的不是
    「没人用的旧货」，是「此刻在服务的那一个」。
  - **只做 ②**（幂等，不补调度）：`/start` 落下的卡仍只靠打开卡片时的
    `/start_session` 补偿 —— 而那条补偿依赖「`list_cards` 不投影 `session_id`」
    这个巧合（§三之二 C），巧合一动就静默停摆。

**为什么「重复调度」本来挡不住。** `IndexingService.schedule_scene_index` 的
`_scene_index_in_flight` 是**并发窗口**去重：任务一结束就 `discard`。而两处调度者
（蒸馏落卡、打开卡片）之间隔着**人操作时间**（点完蒸馏、过一会儿才点开卡），
窗口早关了。去重键挡不住第二拍；只有被索引对象**自己**幂等才挡得住。

**幂等的键怎么从事实推出。** 取**正文指纹**，存在集合自己的 metadata 里
（`scene_indexer._FINGERPRINT_KEY`）—— 场景是正文的纯函数，正文没变就没有任何理由
重建。不另存一份「已索引清单」：那是第二份手工状态，会漂（§四「守卫与被守对象之间
若隔着第二份手工维护的清单，清单就是新的漂移点」）。

**判据落在哪一层。**
  - ①②的幂等：落在**真实运行**上（`SceneIndexer.index_scenes` 驱动假 chroma 客户端，
    数 `delete` / `create` / `add` 的真实调用）—— 隔着 0 层。
  - ①的「都带 indexing_service」：落在**构造点唯一**上（AST，`web/` 下只许 `deps.py`
    拼 `TextManager`）。这是②层代理，**盲区写在下面那条用例的 docstring 里**，
    不假装覆盖。

**本文件不碰真 chroma** —— Windows 宿主对非空集合的任何操作都段错误（同
`tests/test_rag_characters_filter.py` 的处置）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from chromadb.errors import NotFoundError

from core.rag import RAGEngine
from core.scene_indexer import _FINGERPRINT_KEY, SceneIndexer

_REPO = Path(__file__).resolve().parent.parent

TEXT = (
    "第一幕，讲的是魏无羡在云深不知处的一段旧事。那天他坐在廊下，说了很多话，"
    "也做了很多事，直到天色彻底暗下来，才慢慢起身回房。\n\n"
    "第二幕，讲的是江澄后来在莲花坞的日子。他把紫电擦了一遍又一遍，谁也不见，"
    "只让门外的弟子把当日的账册送进来，一直看到深夜才肯停下。"
)
NAME = "scenes_c1"


# ── chroma 替身：只覆盖 index_scenes 用到的三个方法，语义照真件 ──

class _FakeEmbedder:
    """只提供 `_dimensions` —— `RAGEngine.load_existing` 的维度校验读的就是它。"""

    def __init__(self, dims: int) -> None:
        self._dimensions = dims


class _FakeCollection:
    def __init__(self, metadata: dict[str, Any], dims: int) -> None:
        self.metadata = metadata
        self.dims = dims
        self.docs: list[str] = []
        self.adds = 0

    def add(self, documents=None, ids=None, metadatas=None):
        self.docs.extend(documents or [])
        self.adds += 1

    def count(self) -> int:
        return len(self.docs)

    def peek(self, limit: int = 1):
        return {"embeddings": [[0.0] * self.dims] if self.docs else []}


class _FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}
        self.deletes: list[str] = []
        self.creates: list[str] = []

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.collections:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.collections[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        col = _FakeCollection(dict(metadata or {}), getattr(embedding_function, "_dimensions", 0) or 0)
        self.collections[name] = col
        self.creates.append(name)
        return col

    def delete_collection(self, name=None):
        self.deletes.append(name)
        self.collections.pop(name, None)


def _rag(client: _FakeClient, dims: int = 8) -> RAGEngine:
    """不跑 `RAGEngine.__init__`（会碰真 chromadb / 真 embedder），只装用到的字段。"""
    rag = object.__new__(RAGEngine)
    rag._client = client
    rag._embedding_function = _FakeEmbedder(dims)
    rag.collection = None
    rag.collection_name = None
    return rag


def _indexed(client: _FakeClient, text: str = TEXT, dims: int = 8) -> RAGEngine:
    rag = _rag(client, dims=dims)
    n = SceneIndexer().index_scenes(text, rag, "魏无羡", collection_name=NAME)
    assert n > 0, "假件没切出场景 —— 下面的断言会变成对空集的恒真"
    return rag


def _mark(client: _FakeClient) -> tuple[int, int]:
    """首次索引自己也会 delete（删一个不存在的，`except` 吞掉）—— 故数增量，不数总数。"""
    return len(client.deletes), len(client.creates)


# ── ② 幂等 ──

def test_same_text_second_call_does_not_rebuild():
    """锁本体：同正文调两次 → 第二次不 delete、不 create、不 add。

    变异：把幂等检查删掉（恢复无条件 delete + create）→ 本条红。
    """
    client = _FakeClient()
    rag = _rag(client)
    first = SceneIndexer().index_scenes(TEXT, rag, "魏无羡", collection_name=NAME)
    assert first > 0, "假件没切出场景 —— 下面的断言会变成对空集的恒真"
    deletes_0, creates_0 = _mark(client)

    second = SceneIndexer().index_scenes(TEXT, rag, "魏无羡", collection_name=NAME)

    assert second == first, f"第二次应返回同一个场景数，实得 {second} ≠ {first}"
    assert _mark(client) == (deletes_0, creates_0), (
        f"第二次不该删/建集合 —— 删的是此刻可能正在服务的那一个，"
        f"实得 deletes+{len(client.deletes) - deletes_0} creates+{len(client.creates) - creates_0}"
    )
    assert client.collections[NAME].adds == 1, "第二次不该重新嵌入并写入"


def test_changed_text_still_rebuilds():
    """负控：正文变了必须重建 —— 否则这条幂等就退化成「永远不重建」，改一次正文再不刷新。"""
    client = _FakeClient()
    _indexed(client)
    deletes_0, creates_0 = _mark(client)

    changed = TEXT + "\n\n第三幕，讲的是多年以后他们在乱葬岗外的那一次重逢，谁也没有先开口。"
    SceneIndexer().index_scenes(changed, _rag(client), "魏无羡", collection_name=NAME)

    assert _mark(client) == (deletes_0 + 1, creates_0 + 1), (
        f"正文变了却没重建，实得 deletes+{len(client.deletes) - deletes_0} "
        f"creates+{len(client.creates) - creates_0}"
    )


def test_dimension_mismatch_rebuilds_even_when_fingerprint_matches():
    """负控：换过 embedder 的旧集合查询恒失败，指纹命中也不算「已建好」。

    变异：把 `CollectionUnusableError` 也当成复用（直接 return）→ 本条红。
    """
    client = _FakeClient()
    _indexed(client, dims=4)  # 旧 embedder：4 维
    assert client.collections[NAME].metadata.get(_FINGERPRINT_KEY), "旧集合没写上指纹，下面测的就不是这条"
    deletes_0, creates_0 = _mark(client)

    SceneIndexer().index_scenes(TEXT, _rag(client, dims=8), "魏无羡", collection_name=NAME)

    assert _mark(client) == (deletes_0 + 1, creates_0 + 1), \
        "维度不符的旧集合必须重建，不能被指纹命中骗过"


# ── ① 装配出口唯一 ──

def _textmanager_constructions() -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    for path in sorted((_REPO / "web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name == "TextManager":
                found.append((path, node.lineno))
    return found


def test_textmanager_is_only_assembled_in_deps():
    """`web/` 下拼 `TextManager` 的地方只许是 `deps.py`。

    真值层是【装配时有没有漏掉真正的依赖】——`indexing_service` 是唯一一个漏了会
    **静默**的（`/start` 落下的卡从此不调度场景预索引）；本判据读的是【构造点的位置】
    ——**②层代理，盲区记在下一段**。

    由来：`_save_card` 就地拼的那个漏传 `indexing_service`，于是 `/start` 落下的卡
    从不调度场景预索引，只靠 `/start_session` 的补偿。**静默不索引**：没有日志、没有红。

    已知盲区（写明，不假装覆盖）：`from core.text_manager import TextManager as TM`
    这类改名会溜过本判据。**失效方向**：就地构造会**多红一次并点名 file:line**
    （响亮误伤，人看得见）；改名绕过是静默的 —— 故记在这里，而不是补一条更花哨的形状锁。
    """
    sites = _textmanager_constructions()
    assert sites, (
        "`web/` 下一个 `TextManager(...)` 构造点都没找到 —— 空集会让"
        "「所有构造点都在 deps 里」恒真（§四：先问 X 会不会是空集）。"
        "多半是判据本身失效了。"
    )
    deps = _REPO / "web" / "deps.py"
    outside = [(str(p.relative_to(_REPO)).replace("\\", "/"), ln) for p, ln in sites if p != deps]
    assert not outside, (
        f"这些地方在自己拼 `TextManager`：{outside}。\n"
        "唯一的装配出口是 `deps.get_text_manager()` —— 它带齐 `indexing_service` 等全部依赖。\n"
        "就地的构造会**静默**漏掉其中某个：`_save_card` 漏的正是 `indexing_service`，"
        "表现为「场景预索引不再发生」，不报错、也不会有别的东西红。"
    )
