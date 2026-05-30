import urllib.request, json, sys

BASE = "http://localhost:7860"
results = []

def check(num, desc, ok):
    status = "PASS" if ok else "FAIL"
    results.append((num, desc, ok))
    print("  [%s] [#%s] %s" % (status, num, desc))

def req_json(method, path, body=None, headers=None):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    raw = urllib.request.urlopen(r)
    return json.loads(raw.read())

def req_status(method, path, headers=None, body=None):
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        raw = urllib.request.urlopen(r)
        return raw.status
    except urllib.error.HTTPError as e:
        return e.code

# Check 7
s = req_status("GET", "/")
check(7, "GET / returns 200", s == 200)

# Check 8: login with correct password
try:
    login = req_json("POST", "/api/auth/login", {"username": "smoketest_user", "password": "Test1234"})
    token = login.get("access_token", "")
    check(8, "Login returns token", bool(token))
except Exception as e:
    check(8, "Login returns token (FAILED: %s)" % e, False)
    token = ""

# Check 9: login with wrong password
s9 = req_status("POST", "/api/auth/login", body={"username": "nonexistent", "password": "wrong"})
check(9, "Wrong password returns 401", s9 == 401)

# Check 10: market list with token
if token:
    try:
        mkt = req_json("GET", "/api/market/list?page=1&page_size=10", headers={"Authorization": "Bearer " + token})
        ok = "cards" in mkt and "total" in mkt
        check(10, "Market list returns {cards, total}", ok)
    except Exception as e:
        check(10, "Market list failed: %s" % e, False)
else:
    check(10, "Market list (skipped, no token)", False)

# Check 12: admin/dashboard with regular user (expect 403)
s12 = req_status("GET", "/api/admin/dashboard", headers={"Authorization": "Bearer " + token})
check(12, "Regular user gets 403 on admin dashboard", s12 == 403)

# Check 11: admin/dashboard with admin token
# Register an admin user using ADMIN_INVITE_CODE if available, else use config
try:
    reg = req_json("POST", "/api/auth/register", {"username": "admin_test", "password": "Test1234", "invite_code": ""})
    admin_token = reg.get("access_token", "")
    # This might fail if registration is invite-only
    if admin_token:
        # Check if this user is admin
        me = req_json("GET", "/api/auth/me", headers={"Authorization": "Bearer " + admin_token})
        if me.get("is_admin"):
            dash = req_json("GET", "/api/admin/dashboard", headers={"Authorization": "Bearer " + admin_token})
            check(11, "Admin dashboard returns stats", "total_users" in dash)
        else:
            check(11, "Admin dashboard (user not admin)", False)
    else:
        check(11, "Admin dashboard (no admin token)", False)
except:
    check(11, "Admin dashboard (registration may need invite code)", False)

print("\n" + "=" * 40)
passed = sum(1 for _, _, ok in results if ok)
total = len(results)
print("Checklist: %d/%d passed" % (passed, total))
for num, desc, ok in results:
    if not ok:
        print("  [FAIL] [#%s] %s" % (num, desc))
