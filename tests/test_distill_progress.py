"""End-to-end diagnostic: verify distillation progress updates propagate correctly.

Tests the chain: backend progress_pct → API response → monotonic field checks.

Usage:
    python tests/test_distill_progress.py [--base http://localhost:7861]
"""
import urllib.request, urllib.error, json, sys, time, re, os

BASE = "http://localhost:7861"
TEXT_CONTENT = """这是一个测试故事。
从前有一个名叫小明的男孩，他住在一个小村庄里。小明非常勇敢，总是帮助别人。
有一天，小明在森林里遇到了一只受伤的小白兔。小白兔的名字叫小雪，她非常温柔可爱。
小明把小雪带回家，细心地照顾她。渐渐地，他们成了最好的朋友。
村里还有一个女孩叫小红，她是小明的青梅竹马。小红聪明活泼，总是有很多好主意。
三个人一起经历了许多冒险。他们发现了一个神秘的洞穴，里面藏着古老的宝藏。
但是守护宝藏的是一只巨大的石龙。小明的勇气、小雪的温柔、小红的智慧，帮助他们克服了所有困难。
最后，他们成为了村庄里的英雄。这个故事告诉我们，友谊和勇气是最重要的。"""

def req(method, path, headers=None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    raw = urllib.request.urlopen(r)
    return json.loads(raw.read())

def req_status(method, path, headers=None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        raw = urllib.request.urlopen(r)
        return raw.status
    except urllib.error.HTTPError as e:
        return e.code

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=None)
    args = parser.parse_args()
    if args.base:
        global BASE  # noqa: need global to reassign module-level var
        BASE = args.base

    print(f"=" * 70)
    print(f"  DISTILLATION PROGRESS DIAGNOSTIC")
    print(f"  Target: {BASE}")
    print(f"=" * 70)

    # Step 1: Check server is alive
    print(f"\n{'─'*70}")
    print(f"  STEP 1: Server health check")
    print(f"{'─'*70}")
    s = req_status("GET", "/")
    print(f"  GET / → {s}")
    if s != 200 and s != 307:
        print(f"  [FAIL] Server not responding. Is backend running?")
        sys.exit(1)
    print(f"  [PASS] Server is up")

    # Step 2: Login
    print(f"\n{'─'*70}")
    print(f"  STEP 2: Authentication")
    print(f"{'─'*70}")
    token = ""
    try:
        login = req("POST", "/api/auth/login", body={"username": "progress_test", "password": "Test1234"})
        token = login.get("access_token", "")
        print(f"  Login: token={'✓' if token else '✗'}")
    except urllib.error.HTTPError as e:
        # Try register
        try:
            reg = req("POST", "/api/auth/register", body={"username": "progress_test", "password": "Test1234", "invite_code": ""})
            token = reg.get("access_token", "")
            print(f"  Register: token={'✓' if token else '✗'}")
        except Exception as e2:
            print(f"  Register failed: {e2}")

    if not token:
        print(f"  [FAIL] No auth token")
        sys.exit(1)

    h = {"Authorization": f"Bearer {token}"}
    me = req("GET", "/api/auth/me", headers=h)
    print(f"  User: {me.get('username','')} id={me.get('id','')[:8]}")
    print(f"  [PASS] Authenticated")

    # Step 3: Upload a text
    print(f"\n{'─'*70}")
    print(f"  STEP 3: Upload test text")
    print(f"{'─'*70}")
    try:
        upload = req("POST", "/api/text/upload", headers=h, body={
            "title": "蒸馏进度测试文本",
            "content": TEXT_CONTENT,
            "text_type": "story",
        })
        text_id = upload.get("text_id", "")
        print(f"  Upload: text_id={text_id}")
        if not text_id:
            print(f"  [FAIL] No text_id returned")
            sys.exit(1)
    except Exception as e:
        print(f"  Upload failed: {e}")
        sys.exit(1)
    print(f"  [PASS] Text uploaded")

    # Step 4: Start distillation
    print(f"\n{'─'*70}")
    print(f"  STEP 4: Start distillation")
    print(f"{'─'*70}")
    try:
        start = req("POST", "/api/distill/start", headers=h, body={
            "text_id": text_id,
            "character_name": "",
            "force": True,
        })
        task_id = start.get("task_id", "")
        print(f"  Task ID: {task_id}")
        if not task_id:
            print(f"  [FAIL] No task_id returned")
            sys.exit(1)
    except Exception as e:
        print(f"  Start distillation failed: {e}")
        sys.exit(1)
    print(f"  [PASS] Distillation started")

    # Step 5: Poll with detailed output
    print(f"\n{'─'*70}")
    print(f"  STEP 5: Poll task progress (every 2s, up to 120s)")
    print(f"{'─'*70}")
    elapsed_total = 0
    last_pct = -1
    monotonic_ok = True
    max_polls = 60  # 120 seconds ÷ 2s per poll

    for i in range(max_polls):
        time.sleep(2)
        elapsed_total += 2
        try:
            task = req("GET", f"/api/distill/task/{task_id}", headers=h)
        except Exception as e:
            print(f"  [POLL ERROR] {e}")
            continue

        status = task.get("status", "?")
        message = task.get("message", "")
        progress = task.get("progress_pct", 0)
        char = task.get("character", "")
        current = task.get("current", "")
        total = task.get("total", "")

        # Check monotonic
        if progress < last_pct:
            monotonic_ok = False
            print(f"  ⚠️ NON-MONOTONIC: pct dropped from {last_pct}% to {progress}%")
        last_pct = progress

        # Print poll result
        mon_marker = " ✓" if monotonic_ok else " ⚠️"
        print(f"  t={elapsed_total:3d}s | status={status:12s} | msg={message[:50]:50s} | pct={progress:3d}%{mon_marker}")

        if status == "done":
            print(f"\n  [INFO] Task completed after {elapsed_total}s")
            card_id = task.get("card_id", "")
            print(f"  [INFO] Card ID: {card_id}")
            break
        if status == "error":
            print(f"\n  [INFO] Task failed: {message}")
            break

    # Final evaluation
    print(f"\n{'─'*70}")
    print(f"  RESULTS")
    print(f"{'─'*70}")

    if monotonic_ok:
        print(f"  ✓ progress_pct is monotonic (never decreased)")
    else:
        print(f"  ✗ progress_pct decreased at some point")

    if last_pct >= 100:
        print(f"  ✓ progress_pct reached 100%")
    else:
        print(f"  ✗ progress_pct stopped at {last_pct}%")

    print(f"\n{'─'*70}")
    print(f"  SUMMARY")
    print(f"{'─'*70}")
    if monotonic_ok and last_pct == 100:
        print(f"  ✅ PROGRESS SYSTEM WORKING")
        print(f"     - progress_pct monotonic: yes")
        print(f"     - progress_pct final: {last_pct}%")
    elif monotonic_ok:
        print(f"  ⚠️ PARTIAL: monotonic ok but progress stopped at {last_pct}%")
    else:
        print(f"  ❌ PROGRESS SYSTEM NOT WORKING")
        print(f"     - progress_pct decreased or stuck")

    print()

if __name__ == "__main__":
    main()
