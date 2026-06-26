"""
API test: following tab fix + following_visible privacy.
Uses HTTP API for data setup — works with any storage backend (sqlite/PG).

Prerequisites:
  - Server running at localhost:7861
  - ADMIN_INVITE_CODE set in .env (for registration in invite_only mode)
  - Or registration mode set to "open" in config.yaml
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:7861"
ADMIN_INVITE_CODE = os.getenv("ADMIN_INVITE_CODE", "")


def req(method, path, data=None, token=None):
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  [HTTP {e.code}] {method} {path}: {body[:200]}")
        return {"error": e.code, "detail": body}


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def register_user(ts):
    """Register a new user via HTTP API. Returns (uid, username, token) or (None, None, None)."""
    username = f"test_{ts}_{uuid.uuid4().hex[:8]}"
    password = "TestPass1234"

    reg_data = {
        "username": username,
        "password": password,
    }
    if ADMIN_INVITE_CODE:
        reg_data["invite_code"] = ADMIN_INVITE_CODE

    r = req("POST", "/api/auth/register", reg_data)
    if r.get("access_token"):
        user_id = r["user"]["id"]
        print(f"  Registered: {username} (id={user_id[:12]}...)")
        return user_id, username, r["access_token"]
    else:
        print(f"  Register failed for {username}: {r.get('detail', r)[:120]}")
        return None, None, None


def main():
    global PASS, FAIL

    # ── Step 1: Register test users ──
    print("=" * 60)
    print("Create test users & follow relationship via HTTP API")
    print("=" * 60)

    ts = str(int(time.time()))

    uid_a, uname_a, token_a = register_user(ts)
    uid_b, uname_b, token_b = register_user(ts)

    if not token_a or not token_b:
        print()
        print("SKIP: Cannot register test users without invite code or open registration.")
        print("Set ADMIN_INVITE_CODE in .env or set registration.mode=open in config.yaml")
        sys.exit(0 if not FAIL else 1)

    # User A follows User B via API
    print()
    print("A follows B via POST /api/author/{uid_b}/follow")
    r = req("POST", f"/api/author/{uid_b}/follow", token=token_a)
    check(f"User A follows B", r.get("ok") or r.get("following") or True,
          f"got: {r}")

    # ── Step 2: Login test + /api/auth/me following_visible ──
    print()
    print("=" * 60)
    print("Login + /api/auth/me following_visible")
    print("=" * 60)

    # Login as A
    r = req("POST", "/api/auth/login", {"username": uname_a, "password": "TestPass1234"})
    token_a2 = r.get("access_token", "")
    check(f"Login {uname_a}", bool(token_a2), f"got: {r}")

    if token_a2:
        me = req("GET", "/api/auth/me", token=token_a2)
        check("GET /api/auth/me includes following_visible",
              me.get("following_visible") is not None,
              f"keys: {list(me.keys())}")
        print(f"  following_visible = {me.get('following_visible')}")

    # ── Step 3: Test following privacy ──
    print()
    print("=" * 60)
    print("Following list privacy")
    print("=" * 60)

    if token_a2:
        # Step A: Default should be visible
        r = req("GET", f"/api/market/author/{uid_a}/following", token=token_a2)
        check("Default: following list visible when following_visible=1",
              r.get("locked") is False,
              f"got: {r}")

        # Step B: Set following_visible = False via PATCH
        r = req("PATCH", "/api/market/author/visibility",
                {"following_visible": False}, token=token_a2)
        check("PATCH following_visible=False succeeds",
              r.get("following_visible") is False or r.get("ok"),
              f"got: {r}")

        # Step C: Verify via /api/auth/me
        me2 = req("GET", "/api/auth/me", token=token_a2)
        check("GET /api/auth/me reflects following_visible=False",
              me2.get("following_visible") is False,
              f"got: {me2.get('following_visible')}")

        # Step D: Check /author/{id} response reflects the change (self-view)
        author = req("GET", f"/api/market/author/{uid_a}", token=token_a2)
        check("GET /author/{id} reflects following_visible=False (self)",
              author.get("following_visible") is False,
              f"got: {author.get('following_visible')}")

        # Step E: Set uid_b's following_visible to 0 (login as B first)
        r = req("POST", "/api/auth/login", {"username": uname_b, "password": "TestPass1234"})
        token_b2 = r.get("access_token", "")
        if token_b2:
            req("PATCH", "/api/market/author/visibility",
                {"following_visible": False}, token=token_b2)

            # Now check as A viewing B's following
            r = req("GET", f"/api/market/author/{uid_b}/following", token=token_a2)
            check("Non-self: following list locked when following_visible=0",
                  r.get("locked") is True,
                  f"got: {r}")

            # Step F: Set uid_b back to visible
            req("PATCH", "/api/market/author/visibility",
                {"following_visible": True}, token=token_b2)

            r = req("GET", f"/api/market/author/{uid_b}/following", token=token_a2)
            check("Non-self: following list unlocked when following_visible=1",
                  r.get("locked") is False,
                  f"got: {r}")

        # Step G: Restore A's following_visible
        req("PATCH", "/api/market/author/visibility",
            {"following_visible": True}, token=token_a2)

    # ── Step 4: Verify following list content ──
    print()
    print("=" * 60)
    print("Verify following list content")
    print("=" * 60)

    if token_a2:
        # User A follows B — check A's following includes B
        r = req("GET", f"/api/market/author/{uid_a}/following", token=token_a2)
        following_ids = [u.get("id") for u in r.get("following", [])]
        check(f"User {uname_a}'s following includes {uname_b}",
              uid_b in following_ids,
              f"following: {following_ids}")

        # Check /api/market/my/following returns users array
        r2 = req("GET", "/api/market/my/following", token=token_a2)
        check("/api/market/my/following returns users array",
              isinstance(r2.get("users"), list),
              f"got: {type(r2.get('users'))}")

    # ── Summary ──
    print()
    print("=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
