"""
API test: following tab fix + following_visible privacy.
Uses existing users + direct DB setup (registration is invite_only).
"""
import json, sys, urllib.request, urllib.error, time, uuid, sqlite3

BASE = "http://localhost:7861"
DB = "data/character_sim.db"

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

# ── Step 1: Database schema check ──
print("=" * 60)
print("STEP 1: following_visible column exists")
print("=" * 60)
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("PRAGMA table_info(users)")
cols = [c[1] for c in cur.fetchall()]
check("following_visible in users table", "following_visible" in cols)

# ── Create test users directly ──
print()
print("=" * 60)
print("STEP 2: Create test users & follow relationship")
print("=" * 60)

ts = str(int(time.time()))
uid_a = uuid.uuid4().hex[:16]
uid_b = uuid.uuid4().hex[:16]
uname_a = f"test_a_{ts}"
uname_b = f"test_b_{ts}"

cur.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (uid_a, uname_a, "hash_a"))
cur.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (uid_b, uname_b, "hash_b"))
conn.commit()

# User A follows User B
cur.execute("DELETE FROM user_follows WHERE follower_id = ?", (uid_a,))
cur.execute("INSERT INTO user_follows (follower_id, following_id) VALUES (?, ?)", (uid_a, uid_b))
conn.commit()
print(f"  Created: {uname_a} -> follows -> {uname_b}")
conn.close()

# Since we can't actually log in via API (hashed password unknown),
# we need a different approach. Let's create users THEN reset password.
# Actually, let's use a simpler approach: verify the API endpoints
# by creating users whose password we know.
# Let me generate an argon2 hash and insert a user I can log in with.

print()
print("=" * 60)
print("STEP 3: Create login-able test user (setting known password hash)")
print("=" * 60)

# Generate a test user with a known argon2 hash for "testpass1234"
# Pre-computed argon2 hash for password "testpass1234"
try:
    from argon2 import PasswordHasher
    ph = PasswordHasher()
    known_hash = ph.hash("testpass1234")

    conn2 = sqlite3.connect(DB)
    cur2 = conn2.cursor()
    uid_login = uuid.uuid4().hex[:16]
    login_user = f"login_test_{ts}"
    cur2.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (uid_login, login_user, known_hash))
    conn2.commit()
    conn2.close()
    print(f"  Created user: {login_user} (password: testpass1234)")
except ImportError:
    print("  argon2 not installed, skipping login test")
    login_user = None
    uid_login = None

# ── Step 4: Login test ──
print()
print("=" * 60)
print("STEP 4: Login + /api/auth/me following_visible")
print("=" * 60)

token = None
if login_user:
    r = req("POST", "/api/auth/login", {"username": login_user, "password": "testpass1234"})
    token = r.get("access_token", "")
    check(f"Login {login_user}", bool(token), f"got: {r}")

if token:
    me = req("GET", "/api/auth/me", token=token)
    check("GET /api/auth/me includes following_visible",
          me.get("following_visible") is not None,
          f"keys: {list(me.keys())}")
    fv = me.get("following_visible")
    print(f"  following_visible = {fv}")

# ── Step 5: Test following privacy ──
print()
print("=" * 60)
print("STEP 5: Following list privacy")
print("=" * 60)

if token:
    # Step A: Check default is visible
    r = req("GET", f"/api/market/author/{uid_login}/following", token=token)
    check("Default: following list visible when following_visible=1",
          r.get("locked") is False,
          f"got: {r}")

    # Step B: Set following_visible = False via PATCH
    r = req("PATCH", "/api/market/author/visibility",
            {"following_visible": False}, token=token)
    check("PATCH following_visible=False succeeds",
          r.get("following_visible") is False or r.get("ok"),
          f"got: {r}")

    # Step C: Verify via /api/auth/me
    me2 = req("GET", "/api/auth/me", token=token)
    check("GET /api/auth/me reflects following_visible=False",
          me2.get("following_visible") is False,
          f"got: {me2.get('following_visible')}")

    # Step D: Need another user to verify the lock.
    # Since we can only login as this one user, check the /author/{id} response instead
    author = req("GET", f"/api/market/author/{uid_login}", token=token)
    check("GET /author/{id} reflects following_visible=False (self)",
          author.get("following_visible") is False,
          f"got: {author.get('following_visible')}")

    # Step E: Also verify that get_author_following respects the lock for a non-self user
    # We know uid_b exists. First set following_visible for uid_b to 0
    conn3 = sqlite3.connect(DB)
    cur3 = conn3.cursor()
    cur3.execute("UPDATE users SET following_visible = 0 WHERE id = ?", (uid_b,))
    conn3.commit()

    # Now check as login_user viewing uid_b's following
    r = req("GET", f"/api/market/author/{uid_b}/following", token=token)
    check("Non-self: following list locked when following_visible=0",
          r.get("locked") is True,
          f"got: {r}")

    # Step F: Set uid_b back to visible, verify it's unlocked
    cur3.execute("UPDATE users SET following_visible = 1 WHERE id = ?", (uid_b,))
    conn3.commit()
    conn3.close()

    r = req("GET", f"/api/market/author/{uid_b}/following", token=token)
    # uid_b has no follows (only our test user follows uid_b), so list should be empty but not locked
    check("Non-self: following list unlocked when following_visible=1",
          r.get("locked") is False,
          f"got: {r}")

    # Step G: Restore own following_visible
    req("PATCH", "/api/market/author/visibility",
        {"following_visible": True}, token=token)

# ── Step 6: Test following endpoint returns correct data for A->B ──
print()
print("=" * 60)
print("STEP 6: Verify following list content")
print("=" * 60)

if token:
    # User A follows B. Check A's following includes B.
    r = req("GET", f"/api/market/author/{uid_a}/following", token=token)
    following_ids = [u.get("id") for u in r.get("following", [])]
    check(f"User {uname_a}'s following includes {uname_b}",
          uid_b in following_ids,
          f"following: {following_ids}")

    # Check that following endpoint properly handles "users" key format (from /my/following)
    r2 = req("GET", "/api/market/my/following", token=token)
    # This is the current user's following
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
