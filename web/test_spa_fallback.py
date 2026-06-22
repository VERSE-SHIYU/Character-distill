"""Self-test for SPA fallback route order fix.

Verifies:
1. /assets/x.js returns JS content (static mount beats catch-all)
2. /中文.png returns the file (serve_spa real-file fallback)
3. /somepage returns index.html (SPA fallback)
4. /api/whatever returns 404
5. Path traversal is blocked
"""

import os
import sys
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WEB_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_WEB_DIR))

import pytest
from fastapi.testclient import TestClient


# ---- Create test files in _STATIC_DIR ----
from server import app, _STATIC_DIR

_TEST_FILES: list[Path] = []


def _setup():
    js_file = _STATIC_DIR / "assets" / "x.js"
    js_file.parent.mkdir(parents=True, exist_ok=True)
    js_file.write_text("console.log('hello from test');", encoding="utf-8")
    _TEST_FILES.append(js_file)

    cn_file = _STATIC_DIR / "中文.png"
    cn_file.write_bytes(b"fake-png-data")
    _TEST_FILES.append(cn_file)


def _teardown():
    for f in _TEST_FILES:
        if f.exists():
            f.unlink()
    # Remove assets dir if we created it and it's empty
    assets_dir = _STATIC_DIR / "assets"
    try:
        leftover = list(assets_dir.iterdir())
        if not leftover:
            assets_dir.rmdir()
    except (OSError, FileNotFoundError):
        pass


_setup()

client = TestClient(app)


def test_assets_js_returns_content():
    """Static assets mount beats the catch-all."""
    resp = client.get("/assets/x.js")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert "hello from test" in resp.text


def test_chinese_filename_returns_file():
    """Root-level real file served by serve_spa fallback."""
    resp = client.get("/%E4%B8%AD%E6%96%87.png")  # URL-encoded 中文.png
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
    assert resp.content == b"fake-png-data"


def test_somepage_returns_index_html():
    """Unknown SPA route returns index.html."""
    resp = client.get("/somepage")
    assert resp.status_code == 200
    assert "<!DOCTYPE html>" in resp.text.lower() or "<html" in resp.text.lower()


def test_api_prefix_not_index_html():
    """API routes not caught by the SPA fallback — returns error, not index.html."""
    resp = client.get("/api/whatever")
    # Auth middleware returns 401 for unauthenticated /api/* paths.
    # The key invariant: NOT 200 with HTML content.
    assert resp.status_code != 200, f"Expected non-200, got {resp.status_code}"
    assert "text/html" not in (resp.headers.get("content-type") or "")


def test_path_traversal_blocked():
    """Path traversal returns index.html, not the traversed file."""
    resp = client.get("/../../../etc/passwd")
    assert resp.status_code == 200
    # Should return index.html, NOT /etc/passwd content
    assert "root:" not in resp.text  # Not leaking /etc/passwd


if __name__ == "__main__":
    try:
        _setup()
        test_assets_js_returns_content()
        print("PASS: /assets/x.js returns JS content")
        test_chinese_filename_returns_file()
        print("PASS: /中文.png returns file")
        test_somepage_returns_index_html()
        print("PASS: /somepage returns index.html")
        test_api_prefix_not_index_html()
        print("PASS: /api/whatever not index.html")
        test_path_traversal_blocked()
        print("PASS: path traversal blocked")
        print("\nAll 5 tests passed.")
    finally:
        _teardown()
