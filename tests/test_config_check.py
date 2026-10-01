"""启动配置校验 `web/config_check.py`：lifespan 与部署前置门共用的那一处。

lifespan 接线由 test_error_reporting::test_startup_failure_is_reported_before_the_client_closes 覆盖
（它让 `validate_config` 抛错并断言启动失败）。
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

@pytest.mark.parametrize("url", [None, "", "https://peer-node", "HTTPS://Peer-Node:7860/"])
def test_peer_url_allowed(monkeypatch, url):
    if url is None:
        monkeypatch.delenv("PEER_NODE_URL", raising=False)
    else:
        monkeypatch.setenv("PEER_NODE_URL", url)
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
    env.update(JWT_SECRET="j" * 32, PEER_NODE_URL=peer_url)
    return subprocess.run([sys.executable, "-m", "web.config_check"], cwd=_REPO, env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_passes_on_valid_config():
    proc = _cli("https://peer-node")
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
