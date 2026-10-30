"""D17 目标检查：嵌入 / 重排连接在空闲超过 5 s 后仍被复用。

测的是「达成目标」，不是「符合实现」：

1. ``DashScopeEmbedding`` 底层连接池的空闲保活 = ``EMBED_KEEPALIVE_S``（默认 300 s），
   且 socket 选项含 ``SO_KEEPALIVE``。
2. ``with_options(max_retries=0)`` 克隆与原客户端共用同一个底层 httpx 客户端。
3. ``reranker._client(region)`` 的连接池保活同样是 ``EMBED_KEEPALIVE_S``，socket 选项
   含 ``SO_KEEPALIVE``。
4. 同一个 ``DashScopeEmbedding`` 实例两次请求间隔 6 s（> 旧默认 5 s），本地回环服务端
   只应看到 1 条 TCP 连接。旧代码会建 2 条。
5. ``_keepalive_from_env()``：``EMBED_KEEPALIVE_S`` 可覆盖，未设时取默认 300。

实现前用例 1/3/4/5 是红的：1/3/5 因 ``core.http_keepalive`` 尚不存在而 import 失败，
4 因旧默认 5 s 到期后重连而 accept 计数为 2。用例 2 现状即绿，是本 PR 不得破坏的结构。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import adapters.llm_adapter as la
import core.embeddings as embeddings_mod
import core.reranker as reranker


def _has_so_keepalive(options) -> bool:
    return any(
        opt[0] == socket.SOL_SOCKET and opt[1] == socket.SO_KEEPALIVE
        for opt in (options or ())
    )


def test_embedding_pool_keepalive_and_socket_options():
    from core.http_keepalive import EMBED_KEEPALIVE_S

    emb = embeddings_mod.DashScopeEmbedding("k", "cn")
    pool = emb._client._client._transport._pool
    assert pool._keepalive_expiry == EMBED_KEEPALIVE_S == 300.0
    assert _has_so_keepalive(pool._socket_options)


def test_with_options_shares_underlying_httpx_client():
    emb = embeddings_mod.DashScopeEmbedding("k", "cn")
    assert emb._client_no_retry._client is emb._client._client


def test_rerank_pool_keepalive_and_socket_options():
    from core.http_keepalive import EMBED_KEEPALIVE_S

    reranker._CLIENTS.pop("cn", None)
    try:
        pool = reranker._client("cn")._transport._pool
        assert pool._keepalive_expiry == EMBED_KEEPALIVE_S
        assert _has_so_keepalive(pool._socket_options)
    finally:
        client = reranker._CLIENTS.pop("cn", None)
        if client is not None:
            client.close()


class _CountingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accepts = 0

    def get_request(self):
        conn, addr = super().get_request()
        self.accepts += 1
        return conn, addr


class _EmbeddingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        inputs = body.get("input")
        n = len(inputs) if isinstance(inputs, list) else 1
        payload = json.dumps(
            {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.0] * 1024}
                    for i in range(n)
                ],
                "model": body.get("model", "text-embedding-v4"),
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_two_requests_six_seconds_apart_reuse_one_tcp_connection(monkeypatch):
    monkeypatch.setattr(la, "_call_guard", None, raising=False)
    server = _CountingServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "EMBEDDING_BASE_URL",
            f"http://127.0.0.1:{server.server_address[1]}/v1",
        )
        emb = embeddings_mod.DashScopeEmbedding("k", "cn")
        emb._call_api(["first"])
        time.sleep(6)
        emb._call_api(["second"])
        assert server.accepts == 1
    finally:
        server.shutdown()
        server.server_close()


def test_keepalive_env_override(monkeypatch):
    from core.http_keepalive import _keepalive_from_env

    monkeypatch.setenv("EMBED_KEEPALIVE_S", "30")
    assert _keepalive_from_env() == 30.0
    monkeypatch.delenv("EMBED_KEEPALIVE_S")
    assert _keepalive_from_env() == 300.0
