"""一次性变异驱动：逐条改坏 → 跑指定测试 → 记红源 → 逐字节还原。

用法：在仓库根目录运行 `python tests/perf/outbox_mutations.py`。
"""
import hashlib, pathlib, re, subprocess, sys

WT = pathlib.Path.cwd()
PY = sys.executable

BACK = ["tests/test_cross_border_outbox.py", "tests/test_cross_border_sync.py",
        "tests/test_storage_contract_shape.py"]
FRONT = ["src/components/__tests__/AdminPanelPeerRows.test.jsx"]

M = [
 ("O1 生成邀请码不入队", "storage/postgres_store.py",
  "                    if propagate:\n                        await _outbox_put(conn, \"invite_create\"",
  "                    if False:\n                        await _outbox_put(conn, \"invite_create\"", "B"),
 ("O2 同一个码不按顺序（不拦后续）", "web/cross_border_sync.py",
  "            if key in blocked:\n                continue", "            if False:\n                continue", "B"),
 ("O3 没确认也删行", "web/cross_border_sync.py",
  "            if not ok:\n                blocked.add(key)\n                continue\n", "            if not ok:\n                blocked.add(key)\n", "B"),
 ("O4 占码不判是否已被用", "storage/postgres_store.py",
  "                               WHERE code = $3 AND used_by IS NULL RETURNING code\"\"\",",
  "                               WHERE code = $3 RETURNING code\"\"\",", "B"),
 ("O5 占码失败不抛", "storage/postgres_store.py",
  "                        if claimed is None:\n                            raise InviteCodeUnavailable",
  "                        if False:\n                            raise InviteCodeUnavailable", "B"),
 ("O6 已使用带上使用者身份", "storage/postgres_store.py",
  "await _outbox_put(conn, \"invite_used\", invite_code, {\"code\": invite_code})",
  "await _outbox_put(conn, \"invite_used\", invite_code, {\"code\": invite_code, \"used_by\": id})", "B"),
 ("O7 资料带上昵称", "storage/postgres_store.py",
  "            \"avatar_data\": avatar_data or \"\"}",
  "            \"avatar_data\": avatar_data or \"\", \"nickname\": \"\"}", "B"),
 ("O8 头像更新不覆盖旧的待发", "storage/postgres_store.py",
  "                                             avatar_data),\n                            replace=True)",
  "                                             avatar_data),\n                            replace=False)", "B"),
 ("O9 对端已使用覆盖本机记录", "storage/postgres_store.py",
  "                       WHERE code = $2 AND used_by IS NULL\"\"\",",
  "                       WHERE code = $2\"\"\",", "B"),
 ("O10 接收端收到新码也入队（回传）", "web/routers/inter_node.py",
  "    await storage.create_invite_code(code, created_by, propagate=False)",
  "    await storage.create_invite_code(code, created_by, propagate=True)", "B"),
 ("O11 管理后台生成不入队", "web/routers/admin.py",
  "        record = await storage.create_invite_code(code, admin_user[\"id\"], propagate=True)",
  "        record = await storage.create_invite_code(code, admin_user[\"id\"], propagate=False)", "B"),
 ("O12 批量删已使用不入队", "storage/postgres_store.py",
  "                    if propagate:\n                        for r in rows:",
  "                    if False:\n                        for r in rows:", "B"),
 ("O13 注册路由不接 InviteCodeUnavailable", "web/routers/auth.py",
  "    except InviteCodeUnavailable:\n        raise HTTPException(400, \"邀请码已被使用\")\n", "", "B"),
 ("O14 注册把函数当地域传（原缺陷形态）", "web/routers/auth.py",
  "            user_id, username, password_hash, email=email, home_region=home_region,",
  "            user_id, username, password_hash, email=email, home_region=node_region,", "B"),
 ("O15 SQLite create_user 签名漂移", "storage/sqlite_store.py",
  "                          email: str = \"\", home_region: str = \"\", invite_code: str = \"\") -> dict:",
  "                          email: str = \"\", home_region: str = \"\", invite_code: str = \"x\") -> dict:", "B"),
]


def run(kind):
    if kind == "B":
        r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:randomly", *BACK],
                           cwd=WT, capture_output=True, text=True)
        return sorted(set(re.findall(r"^FAILED (\S+)", r.stdout, re.M))), r.stdout.strip().splitlines()[-1]
    r = subprocess.run(["npx", "vitest", "run", *FRONT], cwd=WT / "web/frontend",
                       capture_output=True, text=True)
    out = r.stdout + r.stderr
    if "Startup Error" in out or "Failed to load url" in out:
        raise SystemExit("vitest 没跑起来")
    return sorted(set(re.findall(r"× (.+?) \d+ms", out))), [l for l in out.splitlines() if "Tests " in l][-1]


base_b = run("B")
assert not base_b[0], ("基线不绿", base_b)
print("基线:", base_b[1])
for name, rel, old, new, kind in M:
    p = WT / rel
    src = p.read_bytes()
    h = hashlib.sha256(src).hexdigest()
    t = src.decode()
    assert t.count(old) == 1, (name, t.count(old))
    p.write_text(t.replace(old, new))
    try:
        reds, summ = run(kind)
    finally:
        p.write_bytes(src)
        assert hashlib.sha256(p.read_bytes()).hexdigest() == h
    print(f"{name} -> {'存活!' if not reds else ''}{summ}")
    for x in reds:
        print("   ", x)
