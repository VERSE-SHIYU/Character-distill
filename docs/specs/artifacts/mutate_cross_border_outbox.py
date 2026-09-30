"""变异预跑（跨境发件箱：邀请码三类 + 删除补发的顺序与确认）：逐条改坏一处，对应测试必须红在
**指定的那句断言**上；跑完逐字节还原。

用法：在仓库根目录 `python docs/specs/artifacts/mutate_cross_border_outbox.py`
（需 TEST_DATABASE_URL 指向测试 PG）。

执行框架与判档不在本文件里：改文件 / 跑 / 还原用 `tests/perf/route_facts_mutations.py`
（`_apply` 锚点恰一命中、`_run` 取汇总行与断言行、`_restore`），基线门与判档用
`tests/lock_coverage.py`。本文件只放变异表。

**不放 `tests/perf/`**：那里的 `*_mutations.py` 是登记进覆盖闭合元锁的常设驱动，每个都要配
`*_red_lines.json` 产物（`tests/test_lock_coverage.py::test_every_mutation_driver_has_an_artifact_and_vice_versa`）；
这份是一次性对账。用户资料的变异在 `mutate_profile_outbox.py`（资料走版本戳，见
`storage.base.USER_PROFILE_OP`），这里不重复。

退出码：0 = 全部红在预期断言上；1 = 有存活或红错了地方；2 / 3 = 基线红 / 基线跑不起来（拒跑）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))
sys.path.insert(0, str(ROOT / "tests"))

import lock_coverage  # noqa: E402  —— 判档与基线门的唯一一份实现
import route_facts_mutations as framework  # noqa: E402  —— 执行框架的唯一一份实现

PG = ROOT / "storage/postgres_store.py"
CB = ROOT / "web/cross_border_sync.py"
INTER = ROOT / "web/routers/inter_node.py"
ADMIN = ROOT / "web/routers/admin.py"
AUTH = ROOT / "web/routers/auth.py"
SQLITE = ROOT / "storage/sqlite_store.py"
BACKFILL = ROOT / "storage/migrations_pg/032_outbox_backfill.sql"
TARGETS = (PG, CB, INTER, ADMIN, AUTH, SQLITE, BACKFILL)

# (编号 + 命题, 靶子测试, [(动作, 文件, 载荷)], 红源标记 —— 必须出现在断言行里)
MUTANTS = [
    ('O1 生成邀请码不入队', 'tests/test_cross_border_outbox.py::test_new_invite_code_is_queued_then_sent_and_removed',
     [("repl", PG, [('                    if propagate:\n                        await _outbox_put(conn, "invite_create"',
                        '                    if False:\n                        await _outbox_put(conn, "invite_create"')])],
     '生成邀请码没有在同一事务里入队'),
    ('O2 同一个码不按顺序（不拦后续）', 'tests/test_cross_border_outbox.py::test_same_code_keeps_order_when_the_first_send_fails',
     [("repl", CB, [('            if key in blocked:\n                continue',
                        '            if False:\n                continue')])],
     '新增还没送到，删除就先送了'),
    ('O3 没确认也删行', 'tests/test_cross_border_outbox.py::test_peer_down_keeps_the_row_and_next_round_delivers',
     [("repl", CB, [('            if not ok:\n                blocked.add(key)\n                continue\n',
                        '            if not ok:\n                blocked.add(key)\n')])],
     '没确认就不许删'),
    ('O4 占码不判是否已被用', 'tests/test_cross_border_outbox.py::test_a_code_can_be_claimed_only_once_even_concurrently',
     [("repl", PG, [('                               WHERE code = $3 AND used_by IS NULL RETURNING code""",',
                        '                               WHERE code = $3 RETURNING code""",')])],
     '同一个码被占用了不止一次'),
    ('O5 占码失败不抛', 'tests/test_cross_border_outbox.py::test_a_code_can_be_claimed_only_once_even_concurrently',
     [("repl", PG, [('                        if claimed is None:\n                            raise InviteCodeUnavailable',
                        '                        if False:\n                            raise InviteCodeUnavailable')])],
     '同一个码被占用了不止一次'),
    ('O6 已使用带上使用者身份', 'tests/test_cross_border_outbox.py::test_registration_claims_the_code_and_queues_profile_and_used',
     [("repl", PG, [('await _outbox_put(conn, "invite_used", invite_code, {"code": invite_code})',
                        'await _outbox_put(conn, "invite_used", invite_code, {"code": invite_code, "used_by": id})')])],
     '只同步「已使用」'),
    ('O9 对端已使用覆盖本机记录', 'tests/test_cross_border_outbox.py::test_peer_used_marks_once_and_never_echoes',
     [("repl", PG, [('                       WHERE code = $2 AND used_by IS NULL""",',
                        '                       WHERE code = $2""",')])],
     '对端的「已使用」覆盖了已有记录'),
    ('O10 接收端收到新码也入队（回传）', 'tests/test_cross_border_outbox.py::test_changes_received_from_the_peer_are_not_echoed',
     [("repl", INTER, [('    await storage.create_invite_code(code, created_by, propagate=False)',
                        '    await storage.create_invite_code(code, created_by, propagate=True)')])],
     '从对端收到的变更再入队就会回传给对端'),
    ('O11 管理后台生成不入队', 'tests/test_cross_border_outbox.py::test_admin_invite_routes_queue_and_do_not_send_inline',
     [("repl", ADMIN, [('        record = await storage.create_invite_code(code, admin_user["id"], propagate=True)',
                        '        record = await storage.create_invite_code(code, admin_user["id"], propagate=False)')])],
     '管理后台的生成 / 删除没有按顺序入队'),
    ('O12 批量删已使用不入队', 'tests/test_cross_border_outbox.py::test_delete_used_invites_queues_each_deleted_code',
     [("repl", PG, [('                    if propagate:\n                        for r in rows:',
                        '                    if False:\n                        for r in rows:')])],
     '批量删已使用的码没有逐个入队删除'),
    ('O13 注册路由不接 InviteCodeUnavailable', 'tests/test_cross_border_outbox.py::test_register_route_maps_a_lost_race_to_400',
     [("repl", AUTH, [('    except InviteCodeUnavailable:\n        raise HTTPException(400, "邀请码已被使用")\n',
                        '')])],
     '用户名已存在'),
    ('O14 注册把函数当地域传（原缺陷形态）', 'tests/test_cross_border_outbox.py::test_register_route_uses_the_transactional_path',
     [("repl", AUTH, [('            user_id, username, password_hash, email=email, home_region=home_region,',
                        '            user_id, username, password_hash, email=email, home_region=node_region,')])],
     '注册失败'),
    ('O15 SQLite create_user 签名漂移', 'tests/test_storage_contract_shape.py::test_impl_signature_matches_base',
     [("repl", SQLITE, [('                          email: str = "", home_region: str = "", invite_code: str = "") -> dict:',
                        '                          email: str = "", home_region: str = "", invite_code: str = "x") -> dict:')])],
     '`create_user` 在 `SQLiteStore` 里的形参表与 `StorageBase` 不同'),
    ('O16 补发把对端标记的已用码也发回去', 'tests/test_cross_border_outbox.py::test_backfill_queues_only_codes_used_by_this_node',
     [("repl", BACKFILL, [("WHERE c.used_by IS NOT NULL AND c.used_by <> 'peer'",
                        'WHERE c.used_by IS NOT NULL')])],
     '入队的「已使用」与本机实际用掉的码对不上'),
]


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    bad = {}
    for lock in sorted({t.split("::")[0] for _, t, _, _ in MUTANTS}):
        summary, _, _, _ = framework._run(lock)
        print(f"  {lock}  {summary}")
        cause = lock_coverage.baseline_verdict(summary)
        if cause:
            bad[lock] = cause
    if bad:
        return lock_coverage.refuse_on_baseline(bad)

    problems: list[str] = []
    for label, test, edits, marker in MUTANTS:
        try:
            framework._apply(edits)
            summary, keep, _, _ = framework._run(test)
        finally:
            framework._restore(baseline)
        got = lock_coverage.outcome(summary)
        on_marker = any(marker in k for k in keep)
        if got != lock_coverage.RED:
            problems.append(f"{label}：实得 {got}（期望 RED）")
        elif not on_marker:
            problems.append(f"{label}：红了，但红源里没有 {marker!r}（红的不是那条断言）")
        print(f"\n### {label}   实得={got}   红源命中={on_marker}")
        for k in keep:
            print("   ", k)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        same = hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256(baseline[p]).digest()
        if not same:
            problems.append(f"{p.relative_to(ROOT)} 还原后 sha256 不符")
        print(f"  {p.relative_to(ROOT)}  {same}")

    print("\n结论：" + ("全部红在预期断言上" if not problems else "有问题"))
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
