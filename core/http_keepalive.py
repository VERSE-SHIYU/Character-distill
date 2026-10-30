"""Embedding / rerank HTTP 连接池的空闲保活（D17，一处定义两处共用）。

为什么是 300 s
--------------
DashScope SDK 的连接复用配置（2026-10-27）写明 Java SDK ``connectionIdleTimeout``
默认 300 s；同页 Python 表的 ``httpx keepalive_expiry`` 默认 5.0 s。boardx/workspacex
#4840（2026-09-30）实测同一客户端空闲 15 / 45 / 90 s 后复用仍为 251 / 241 / 253 ms，
冷连接 618 ms —— 服务端至少保留空闲连接 90 s，他们同样改为 300 s。
https://www.alibabacloud.com/help/zh/model-studio/connection-multiplexing-configuration

上限：百炼前端 ``server: istio-envoy``（D15 响应头），Envoy 空闲超时默认 1 小时。
https://www.envoyproxy.io/docs/envoy/v1.24.2/faq/configuration/timeouts

为什么还要 TCP 保活
--------------------
``SO_KEEPALIVE`` + ``TCP_KEEPIDLE=60`` / ``TCP_KEEPINTVL=60`` / ``TCP_KEEPCNT=5``
照 anthropic-sdk-python v2.3.0 的默认传输层（本机可核对的 0.101.0 同一段在
``anthropic/_base_client.py:850-874``）；openai-python #3269 / #3270 报告「NAT 后无
TCP 保活的请求永久挂起」，提议的也是这组值。作用：空闲时探测包让中间设备不把连接
当空闲删掉；连接已断时内核关掉它，连接池不再复用死连接。
https://github.com/openai/openai-python/issues/3269
https://github.com/openai/openai-python/issues/3270

库现状（仓库锁定版本）：httpx2 2.13.0 ``Limits.keepalive_expiry`` 默认 5.0
（``_config.py:174``）、无 TCP 保活；openai 3.13.0 的
``DEFAULT_CONNECTION_LIMITS`` 也没设保活（``_constants.py:9``）。httpx2 #798 仍
Open、无维护者结论，故由本仓库自行配置。
https://github.com/pydantic/httpx2/issues/798
"""

from __future__ import annotations

import os
import socket


def _keepalive_from_env() -> float:
    return max(float(os.getenv("EMBED_KEEPALIVE_S", "300")), 0.0)


EMBED_KEEPALIVE_S = _keepalive_from_env()


def _socket_options() -> list[tuple[int, int, int]]:
    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, value in (
        ("TCP_KEEPIDLE", 60),
        ("TCP_KEEPINTVL", 60),
        ("TCP_KEEPCNT", 5),
    ):
        opt = getattr(socket, name, None)
        if opt is not None:
            options.append((socket.IPPROTO_TCP, opt, value))
    return options


KEEPALIVE_SOCKET_OPTIONS = _socket_options()
