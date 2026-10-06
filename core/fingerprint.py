"""凭据的不可逆指纹。

单独成模块是为了让轻量调用方（节点间签名 `web/inter_node_auth.py`）不必为一个哈希函数
导入 `core/embeddings.py` —— 那边在模块顶层导入 chromadb。
"""

from __future__ import annotations

import hashlib


def key_fingerprint(key: str) -> str:
    """凭据的不可逆指纹（SHA-256 前 16 位十六进制）：既能当缓存身份，也能进日志。

    缓存身份与日志是同一件事的两面 —— 身份要用整个 key 才区分得开，日志又不能让 key
    露面，于是只留这个指纹。空串是「没配 key」的显式语义，原样返回，调用方的身份表示
    不变（`f"{text_id}:"`）。
    """
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def content_fingerprint(text: str) -> str:
    """正文的不可逆指纹（整段 SHA-256 十六进制）。

    场景索引的幂等键与「这条索引讲的是不是同一份正文」的判据（卡上 `source_fingerprint`
    与集合元数据用同一个值）。**只此一处** —— `scene_indexer` 与 `card_draft` 都调它。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
