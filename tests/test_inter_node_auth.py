"""Tests for inter-node authentication (HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

_WEB_DIR = str(Path(__file__).resolve().parent.parent / "web")
if _WEB_DIR not in sys.path:
    sys.path.insert(0, _WEB_DIR)

import pytest

from inter_node_auth import (
    create_auth_header,
    get_inter_node_secret,
    verify_auth_header,
)


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret")


def test_get_secret():
    assert get_inter_node_secret() == "test-inter-node-secret"


def test_get_secret_missing(monkeypatch):
    monkeypatch.delenv("INTER_NODE_SECRET")
    with pytest.raises(RuntimeError, match="未设置"):
        get_inter_node_secret()


def test_create_and_verify():
    payload = {"user_id": "u123", "action": "sync_user"}
    headers = create_auth_header(payload)
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("HMAC-SHA256 ts=")

    valid, reason = verify_auth_header(headers["Authorization"], payload)
    assert valid, f"Expected valid, got: {reason}"
    assert reason == ""


def test_wrong_payload():
    payload = {"user_id": "u123"}
    headers = create_auth_header(payload)
    valid, reason = verify_auth_header(headers["Authorization"], {"user_id": "hacker"})
    assert not valid
    assert "签名" in reason


def test_missing_header():
    valid, reason = verify_auth_header(None, {})
    assert not valid
    assert "缺少" in reason


def test_bad_format():
    valid, reason = verify_auth_header("Bearer invalid", {})
    assert not valid
    assert "格式错误" in reason


def test_tampered_signature():
    payload = {"user_id": "u123"}
    headers = create_auth_header(payload)
    tampered = headers["Authorization"].rsplit("sig=", 1)[0] + "sig=0000000000"
    valid, reason = verify_auth_header(tampered, payload)
    assert not valid
    assert "签名" in reason


def test_expired_timestamp():
    secret = "test-inter-node-secret"
    payload = {"user_id": "u123"}
    old_ts = int(time.time() * 1000) - 60_000  # 60 s ago (past the 30 s window)
    msg = f"{old_ts}:{json.dumps(payload, separators=(',', ':'), sort_keys=True)}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    auth = f"HMAC-SHA256 ts={old_ts},sig={sig}"

    valid, reason = verify_auth_header(auth, payload)
    assert not valid
    assert "过期" in reason
