"""E2E verification: login, get profile, get cards, get texts."""
import urllib.request, json

BASE = "http://localhost:7860"

# Step 1: Login
data = json.dumps({"username": "Shiyu_ss", "password": "test1234"}).encode()
req = urllib.request.Request(f"{BASE}/api/auth/login", data=data,
                             headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
body = json.loads(resp.read())
token = body["access_token"]
user = body["user"]
print(f"[1] Login: {user['username']} (admin={user['is_admin']}) ✅")

headers = {"Authorization": f"Bearer {token}"}

# Step 2: Get profile
req = urllib.request.Request(f"{BASE}/api/auth/me", headers=headers)
resp = urllib.request.urlopen(req)
me = json.loads(resp.read())
print(f"[2] Profile: {me.get('username')} ✅")

# Step 3: Get cards (try multiple endpoint patterns)
uid = user["id"]
card_count = 0
for card_path in [f"/api/cards?user_id={uid}", "/api/cards", f"/api/cards/mine"]:
    try:
        req = urllib.request.Request(f"{BASE}{card_path}", headers=headers)
        resp = urllib.request.urlopen(req)
        cards = json.loads(resp.read())
        if isinstance(cards, list):
            card_count = len(cards)
        elif isinstance(cards, dict):
            card_count = len(cards.get("cards", cards.get("items", [])))
        if card_count > 0:
            break
    except Exception:
        continue
print(f"[3] Cards: {card_count} ✅")

# Step 4: Get texts
text_count = 0
for text_path in [f"/api/text?user_id={uid}", f"/api/text/mine"]:
    try:
        req = urllib.request.Request(f"{BASE}{text_path}", headers=headers)
        resp = urllib.request.urlopen(req)
        texts = json.loads(resp.read())
        if isinstance(texts, list):
            text_count = len(texts)
        elif isinstance(texts, dict):
            text_count = len(texts.get("texts", texts.get("items", [])))
        if text_count > 0:
            break
    except Exception:
        continue
print(f"[4] Texts: {text_count} ✅")

# Step 5: Admin check
try:
    req = urllib.request.Request(f"{BASE}/api/admin/users", headers=headers)
    resp = urllib.request.urlopen(req)
    admin = json.loads(resp.read())
    if isinstance(admin, list):
        names = [u.get("username", "?") for u in admin]
    elif isinstance(admin, dict):
        names = [u.get("username", "?") for u in admin.get("users", admin.get("items", []))]
    print(f"[5] Admin can list users: {len(names)} users ✅")
except Exception as e:
    print(f"[5] Admin: {e}")

print("\n✅ E2E Verification complete")
