# -*- coding: utf-8 -*-
"""节点间配置启动期校验（spec peer-config-guard 设计 A）。

两个判据：
  1. `validate_peer_config` 只在 PEER_NODE_URL 非空时收紧：https / 有主机名 / 无路径 /
     无 query / 无 userinfo，且 INTER_NODE_SECRET、INTER_NODE_SELF_HOST 齐备，且本机
     域名不等于对端主机名（主域名按线路分流会解析回本机，见 PR #102）。
  2. 校验在 lifespan 里、`_reconcile_distill_tasks()`（第一个碰存储的地方）之前 ——
     坏配置起不来，且失败时库还没迁移。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import peer_client
import server

SECRET = "test-inter-node-secret-0123456789abcdef"
JWT = "test-jwt-secret-0123456789abcdef0123456789abcdef"


def _set_env(monkeypatch, *, peer_url, self_host=None, secret=SECRET):
    if peer_url:
        monkeypatch.setenv("PEER_NODE_URL", peer_url)
    else:
        monkeypatch.delenv("PEER_NODE_URL", raising=False)
    if self_host:
        monkeypatch.setenv("INTER_NODE_SELF_HOST", self_host)
    else:
        monkeypatch.delenv("INTER_NODE_SELF_HOST", raising=False)
    if secret:
        monkeypatch.setenv("INTER_NODE_SECRET", secret)
    else:
        monkeypatch.delenv("INTER_NODE_SECRET", raising=False)


@pytest.mark.parametrize(
    "peer_url, self_host, secret, ok",
    [
        ("", None, SECRET, True),                                              # 单节点
        ("https://sg.bookecho-shiyu.cn", "sz.bookecho-shiyu.cn", SECRET, True),  # 正常
        ("http://sg.bookecho-shiyu.cn", "sz.bookecho-shiyu.cn", SECRET, False),  # http
        ("https://sg.bookecho-shiyu.cn/path", "sz.bookecho-shiyu.cn", SECRET, False),  # 带路径
        ("https://sg.bookecho-shiyu.cn", "sg.bookecho-shiyu.cn", SECRET, False),  # 与本机同名
        ("https://sg.bookecho-shiyu.cn", None, SECRET, False),                   # 缺 SELF_HOST
        ("https://sg.bookecho-shiyu.cn", "sz.bookecho-shiyu.cn", None, False),   # 缺密钥
    ],
    ids=["empty", "valid", "http", "path", "self_host_equal",
         "missing_self_host", "missing_secret"],
)
def test_validate_peer_config(monkeypatch, peer_url, self_host, secret, ok):
    _set_env(monkeypatch, peer_url=peer_url, self_host=self_host, secret=secret)
    if ok:
        peer_client.validate_peer_config()
    else:
        with pytest.raises(RuntimeError):
            peer_client.validate_peer_config()


def test_startup_rejects_bad_peer_config_before_storage(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT)
    _set_env(monkeypatch, peer_url="http://sg.bookecho-shiyu.cn",
             self_host="sz.bookecho-shiyu.cn", secret=SECRET)

    touched: dict[str, bool] = {}

    async def _reconcile():
        touched["storage"] = True

    monkeypatch.setattr(server, "_reconcile_distill_tasks", _reconcile)
    with pytest.raises(peer_client.PeerConfigError):
        with TestClient(server.app):
            pass
    assert "storage" not in touched, "校验晚于存储初始化：回滚到 PREV_SHA 前库已被迁移"
