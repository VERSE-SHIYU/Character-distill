# -*- coding: utf-8 -*-
"""voice.py 两处越权：preview_audio 的自定义音色文件、voice_synthesize 的 card_id 参考音频。

修复前二者都不查归属 —— 自定义音色按设计私有（/list 只列 user_customs、删除/上传都查
属主），参考音频的另外三个端点（get_ref_audio / preview_ref_audio / delete_ref_audio）
也都查卡主，唯独这两个绕过了。本测试锁：
  1. 非属主一律 404（不是 403 —— 403 会让人靠状态码枚举资源是否存在）
  2. fail closed：条目缺 user_id 也拒
  3. 属主仍可访问（修复不得把正常路径一起掐了）
"""
import asyncio

import pytest
from fastapi import HTTPException

import routers.voice as V


# ── preview_audio：自定义音色文件 ─────────────────────────────────────

def _library(monkeypatch, entries):
    monkeypatch.setattr(V, "_read_voice_library", lambda: entries)


def test_preview_foreign_custom_voice_is_404(monkeypatch, tmp_path):
    # 文件必须真的存在 —— 否则「404」可能只是因为文件不在，测不出归属校验有没有生效
    (tmp_path / "v1.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(V, "VOICE_LIBRARY_DIR", tmp_path)
    _library(monkeypatch, [{"voice_id": "v1", "ext": ".wav", "user_id": "u1"}])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(V.preview_audio("v1", user={"id": "u2"}, engine=None))
    assert ei.value.status_code == 404


def test_preview_custom_voice_without_owner_is_404(monkeypatch, tmp_path):
    """fail closed：条目缺 user_id 也拒，不因「无从校验」放行。"""
    (tmp_path / "v1.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(V, "VOICE_LIBRARY_DIR", tmp_path)
    _library(monkeypatch, [{"voice_id": "v1", "ext": ".wav"}])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(V.preview_audio("v1", user={"id": "u1"}, engine=None))
    assert ei.value.status_code == 404


def test_preview_owner_reaches_the_file(monkeypatch, tmp_path):
    _library(monkeypatch, [{"voice_id": "v1", "ext": ".wav", "user_id": "u1"}])
    blob = tmp_path / "v1.wav"
    blob.write_bytes(b"RIFF")
    monkeypatch.setattr(V, "VOICE_LIBRARY_DIR", tmp_path)
    resp = asyncio.run(V.preview_audio("v1", user={"id": "u1"}, engine=None))
    assert resp.media_type == "audio/wav"


# ── voice_synthesize：card_id 参考音频 ────────────────────────────────

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _Storage:
    def __init__(self, card):
        self._card = card

    async def get_card(self, card_id):
        return self._card


def _synth(card, body, user):
    # 绕过 slowapi 限流装饰器（它要求真的 starlette Request）；测的是它包着的那个函数。
    endpoint = getattr(V.voice_synthesize, "__wrapped__", V.voice_synthesize)
    return asyncio.run(endpoint(
        _Req(body), user=user, engine=None, storage=_Storage(card), voice_client=None,
    ))


def test_synthesize_foreign_card_is_404():
    card = {"id": "c1", "user_id": "u1"}
    with pytest.raises(HTTPException) as ei:
        _synth(card, {"text": "hi", "card_id": "c1"}, {"id": "u2"})
    assert ei.value.status_code == 404


def test_synthesize_missing_card_is_404():
    with pytest.raises(HTTPException) as ei:
        _synth(None, {"text": "hi", "card_id": "nope"}, {"id": "u1"})
    assert ei.value.status_code == 404


def test_synthesize_card_without_owner_is_404():
    """fail closed：卡缺 user_id 也拒。"""
    with pytest.raises(HTTPException) as ei:
        _synth({"id": "c1"}, {"text": "hi", "card_id": "c1"}, {"id": "u1"})
    assert ei.value.status_code == 404


def test_synthesize_without_card_id_is_unaffected(monkeypatch):
    """不传 card_id 走 Edge TTS，不该被这道门拦。"""
    class _Engine:
        async def synthesize(self, text, voice):
            return b"mp3"

    endpoint = getattr(V.voice_synthesize, "__wrapped__", V.voice_synthesize)
    got = asyncio.run(endpoint(
        _Req({"text": "hi"}), user={"id": "u1"}, engine=_Engine(),
        storage=_Storage(None), voice_client=None,
    ))
    assert got.body == b"mp3"
