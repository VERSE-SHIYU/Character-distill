# -*- coding: utf-8 -*-
"""面向用户请求的凭据门：没配自己的百炼 key 就 400 提示配置。"""
from __future__ import annotations

from fastapi import HTTPException

from core.embeddings import EMBEDDING_KEY_REQUIRED
from storage.base import StorageBase
from web.llm_resolution import EmbeddingResolution, resolve_embedding


async def require_embedding(user_id: str, storage: StorageBase) -> EmbeddingResolution:
    """面向用户请求的唯一入口：没配自己的百炼 key → 400 + EMBEDDING_KEY_REQUIRED。"""
    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})  # 读库不包 try：失败走全局 500
    if not emb.key:
        raise HTTPException(400, EMBEDDING_KEY_REQUIRED)
    return emb
