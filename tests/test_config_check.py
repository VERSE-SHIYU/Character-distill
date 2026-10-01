"""启动配置校验 `web/config_check.py`：lifespan 与部署前置门共用的那一处。

lifespan 接线由 test_error_reporting::test_startup_failure_is_reported_before_the_client_closes 覆盖
（它让 `validate_config` 抛错并断言启动失败）；「校验先于存储」由本文件末尾的用例覆盖。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from config_check import VALIDATORS, validate_config
from peer_client import PeerNotSecure, validate_peer_node_url

_REPO = Path(__file__).resolve().parent.parent


# ── PEER_NODE_URL ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_peer_env(monkeypatch):
    monkeypatch.delenv("PEER_NODE_URL", raising=False)
    monkeypatch.delenv("INTER_NODE_SELF_HOST", raising=False)


@pytest.mark.parametrize("url, own", [
    (None, None),                                         # 单节点
    ("", "sz.bookecho-shiyu.cn"),                         # 显式留空 = 单节点
    ("https://sg.bookecho-shiyu.cn", "sz.bookecho-shiyu.cn"),
    ("HTTPS://SG.bookecho-shiyu.cn:443/", "SZ.bookecho-shiyu.cn"),
])
def test_peer_url_allowed(monkeypatch, url, own):
    if url is not None:
        monkeypatch.setenv("PEER_NODE_URL", url)
    if own is not None:
        monkeypatch.setenv("INTER_NODE_SELF_HOST", own)
    validate_peer_node_url()


@pytest.mark.parametrize("url, own, needle", [
    ("https:///api", "sz.bookecho-shiyu.cn", "主机名"),
    ("https://sg.bookecho-shiyu.cn", None, "INTER_NODE_SELF_HOST"),
    ("https://sg.bookecho-shiyu.cn", "  ", "INTER_NODE_SELF_HOST"),
    # 两台都填 GeoDNS 主域名：请求发回本机
    ("https://bookecho-shiyu.cn", "bookecho-shiyu.cn", "本节点"),
    ("https://Bookecho-Shiyu.cn:443", "bookecho-shiyu.cn", "本节点"),
    ("https://sz.bookecho-shiyu.cn", "sz.bookecho-shiyu.cn:443", "本节点"),
])
def test_peer_url_rejected_host(monkeypatch, url, own, needle):
    monkeypatch.setenv("PEER_NODE_URL", url)
    if own is not None:
        monkeypatch.setenv("INTER_NODE_SELF_HOST", own)
    with pytest.raises(RuntimeError, match=needle):
        validate_peer_node_url()


@pytest.mark.parametrize("url, scheme", [("http://peer-node", "http"), ("peer-node:7860", "(无协议)")])
def test_peer_url_rejected(monkeypatch, url, scheme):
    monkeypatch.setenv("PEER_NODE_URL", url)
    with pytest.raises(PeerNotSecure, match=rf"PEER_NODE_URL.*当前协议：{re.escape(scheme)}"):
        validate_peer_node_url()


def test_peer_url_is_registered():
    assert validate_peer_node_url in VALIDATORS


# ── 部署入口：`python -m web.config_check`（与 deploy.yml 同一条命令） ─────────

def _cli(peer_url: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("FERNET_KEY", "INTER_NODE_SECRET")}
    env.update(JWT_SECRET="j" * 32, PEER_NODE_URL=peer_url,
               INTER_NODE_SELF_HOST="sz.bookecho-shiyu.cn")
    return subprocess.run([sys.executable, "-m", "web.config_check"], cwd=_REPO, env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_passes_on_valid_config():
    proc = _cli("https://sg.bookecho-shiyu.cn")
    assert proc.returncode == 0, proc.stderr


def test_cli_fails_naming_the_variable():
    proc = _cli("http://peer-node")
    assert proc.returncode == 1
    assert "PEER_NODE_URL" in proc.stderr, proc.stderr


# ── deploy.yml：两台都在 compose up 之前跑校验 ──────────────────────────────

@pytest.mark.parametrize("job", ["deploy-sz", "deploy-sg"])
def test_deploy_validates_before_compose_up(job):
    wf = yaml.safe_load((_REPO / ".github/workflows/deploy.yml").read_text(encoding="utf-8"))
    script = next(s["with"]["script"] for s in wf["jobs"][job]["steps"]
                  if isinstance(s.get("with"), dict) and "script" in s["with"])
    check = script.find("python -m web.config_check")
    up = script.find("docker compose -f docker-compose.prod.yml up -d --remove-orphans")
    assert check != -1 and up != -1, f"负控：{job} 必须同时找到校验与 compose up"
    assert check < up, f"{job}：配置校验必须在 compose up 之前"


def test_validate_config_runs_every_validator(monkeypatch):
    calls = []
    monkeypatch.setattr("config_check.VALIDATORS", tuple(lambda i=i: calls.append(i) for i in range(3)))
    validate_config()
    assert calls == [0, 1, 2]


# ── lifespan：校验先于任何存储访问 ───────────────────────────────────────

def test_lifespan_validates_before_touching_storage(monkeypatch):
    """配置校验必须在第一次取存储之前失败：迁移是首次取存储时懒执行的，
    校验晚了，库已被迁移，回滚到 PREV_SHA 就不再安全（031 改过表名）。"""
    import deps
    import server
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PEER_NODE_URL", "http://sg.bookecho-shiyu.cn")
    monkeypatch.setenv("INTER_NODE_SELF_HOST", "sz.bookecho-shiyu.cn")
    touched: list[int] = []

    def _get_storage():
        touched.append(1)
        raise AssertionError("get_storage 在配置校验之前被调用")

    monkeypatch.setattr(server, "get_storage", _get_storage)
    monkeypatch.setattr(deps, "get_storage", _get_storage)

    with pytest.raises(PeerNotSecure):
        with TestClient(server.app):
            pass
    assert touched == [], "配置校验晚于存储访问"
