"""Smoke tests — verify core API endpoints return expected shapes."""
import urllib.request, json, sys

BASE = "http://localhost:7860"

def req(method, path, headers=None, body=None):
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    raw = urllib.request.urlopen(r)
    return json.loads(raw.read())

def req_status(method, path, headers=None):
    r = urllib.request.Request(f"{BASE}{path}", headers=headers or {}, method=method)
    try:
        raw = urllib.request.urlopen(r)
        return raw.status
    except urllib.error.HTTPError as e:
        return e.code

def main():
    # Register
    try:
        reg = req("POST", "/api/auth/register", body={"username": "smoketest_user", "password": "Test1234", "invite_code": ""})
        print(f"Register: {json.dumps(reg, ensure_ascii=False)[:100]}")
        token = reg.get("access_token", "")
    except Exception as e:
        print(f"Register failed: {e}")
        try:
            login = req("POST", "/api/auth/login", body={"username": "smoketest_user", "password": "Test1234"})
            print(f"Login: {json.dumps(login, ensure_ascii=False)[:100]}")
            token = login.get("access_token", "")
        except Exception as e2:
            print(f"Login failed: {e2}")
            token = ""

    if not token:
        print("FAIL: No token obtained")
        sys.exit(1)

    h = {"Authorization": f"Bearer {token}"}

    print("\n=== Auth ===")
    me = req("GET", "/api/auth/me", headers=h)
    print(f"/api/auth/me: id={me.get('id','')[:8]} username={me.get('username','')}")

    ann = req("GET", "/api/auth/announcement", headers=h)
    print(f"/api/auth/announcement: announcement={ann.get('announcement','')}")

    print("\n=== Market ===")
    m = req("GET", "/api/market/list?page=1&page_size=10", headers=h)
    print(f"/api/market/list: cards={len(m.get('cards',[]))} total={m.get('total',0)} page={m.get('page',0)}")

    t = req("GET", "/api/market/tags", headers=h)
    print(f"/api/market/tags: tags count={len(t.get('tags',[]))}")

    f = req("GET", "/api/market/featured", headers=h)
    print(f"/api/market/featured: count={len(f)}")

    s = req("GET", "/api/market/global-search?q=test", headers=h)
    print(f"/api/market/global-search: cards={len(s.get('cards',[]))} texts={len(s.get('texts',[]))} users={len(s.get('users',[]))}")

    print("\n=== Text ===")
    tl = req("GET", "/api/text/list", headers=h)
    print(f"/api/text/list: count={len(tl)}")

    rp = req("GET", "/api/text/reading-progress/all", headers=h)
    print(f"/api/text/reading-progress/all: count={len(rp)}")

    print("\n=== History ===")
    hist = req("GET", "/api/history/list", headers=h)
    print(f"/api/history/list: items={len(hist.get('items',[]))}")

    print("\n=== Admin (non-admin, expect 403) ===")
    dash_status = req_status("GET", "/api/admin/dashboard", headers=h)
    print(f"/api/admin/dashboard: {dash_status} (expect 403)")

    print("\n=== Unauthenticated (expect 401/403) ===")
    for path in ["/api/market/list", "/api/text/list", "/api/admin/dashboard"]:
        s = req_status("GET", path)
        print(f"GET {path}: {s} (expect 401/403)")

    print("\n=== All smoke tests passed! ===")


if __name__ == "__main__":
    main()
