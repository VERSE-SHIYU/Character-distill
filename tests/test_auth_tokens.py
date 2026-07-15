"""API-level test: login→refresh token chain.

Verifies:
1. Login returns refresh_token as string (not tuple from unpack bug)
2. Refresh endpoint accepts that string and returns 200
3. New refresh_token is also a string

Run:
  TEST_API_BASE=http://localhost:7861 python -m pytest tests/test_auth_tokens.py -v
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE = os.getenv("TEST_API_BASE", "http://localhost:7861")

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_API_BASE") and not os.getenv("CI"),
    reason="requires running server at TEST_API_BASE or CI",
)


def _post(path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_login_refresh_chain():
    """Login as testadmin, verify refresh_token is str, then refresh it."""
    # ── Login ──
    status, body = _post("/api/auth/login", {
        "username": "testadmin",
        "password": "test1234",
    })
    assert status == 200, f"Login failed ({status}): {body}"
    rt = body.get("refresh_token")
    assert isinstance(rt, str), (
        f"refresh_token must be str, got {type(rt).__name__}: {rt}"
    )
    assert len(rt) > 20, f"refresh_token too short: {rt}"

    # ── Refresh ──
    status2, body2 = _post("/api/auth/refresh", {"refresh_token": rt})
    assert status2 == 200, f"Refresh failed ({status2}): {body2}"
    rt2 = body2.get("refresh_token")
    assert isinstance(rt2, str), (
        f"refresh_token after refresh must be str, got {type(rt2).__name__}: {rt2}"
    )
    assert rt2 != rt, "refresh must rotate the token"
    assert len(rt2) > 20, f"refreshed token too short: {rt2}"
