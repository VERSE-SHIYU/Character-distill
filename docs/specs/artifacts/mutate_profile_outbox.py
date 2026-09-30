"""变异预跑：逐条把改后代码改坏一处，跑对应测试，必须红；跑完字节还原。

用法：在仓库根目录 `python docs/specs/artifacts/mutate_profile_outbox.py`
（需 TEST_DATABASE_URL 指向测试 PG）。任何一条存活（测试仍绿）即退出码 1。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

T = "tests/test_profile_outbox.py"
PG, CB, AUTH, MIG = ("storage/postgres_store.py", "web/cross_border_sync.py",
                     "web/routers/auth.py", "storage/migrations_pg/030_backfill_profile_sync.sql")

# (编号, 文件, 原文, 改成, 必须变红的测试)
MUTANTS = [
    ("M1", PG, "                    await _enqueue_profile_sync(conn, id)\n", "",
     f"{T}::test_registration_queues_and_the_loop_sends_the_profile"),
    ("M2", PG, "                    await _enqueue_profile_sync(conn, id)\n                return",
     "                return",  # 同时把入队挪到事务外（下一条替换补上）
     f"{T}::test_failed_create_user_leaves_no_profile_row"),
    ("M3", PG, "ON CONFLICT (op_type, target_id) DO UPDATE SET payload = EXCLUDED.payload\"\"\",\n        USER_PROFILE_OP",
     "ON CONFLICT (op_type, target_id) DO NOTHING\"\"\",\n        USER_PROFILE_OP",
     f"{T}::test_change_during_send_is_sent_next_round"),
    ("M4", PG, "                    if self._parse_rowcount(status):\n                        await _enqueue_profile_sync(conn, user_id)\n", "",
     f"{T}::test_avatar_change_requeues_and_unknown_user_does_not"),
    ("M5", PG, "                    if self._parse_rowcount(status):\n", "                    if True:\n",
     f"{T}::test_avatar_change_requeues_and_unknown_user_does_not"),
    ("M6", PG, "WHERE id = $1 AND payload IS NOT DISTINCT FROM $2\",\n                    id, payload,",
     "WHERE id = $1\",\n                    id,",
     f"{T}::test_change_during_send_is_sent_next_round"),
    ("M7", PG, "payload IS NOT DISTINCT FROM $2", "payload = $2",
     f"{T}::test_acked_row_with_null_payload_is_removed"),
    ("M8", CB, "    if user.get(\"home_region\") != node_region():", "    if False:",
     f"{T}::test_user_not_homed_here_is_dropped_unsent"),
    ("M9", CB, "        logger.info(\"Outbox profile dropped, user no longer exists: %s\", what)\n        return True",
     "        logger.info(\"Outbox profile dropped, user no longer exists: %s\", what)\n        return False",
     f"{T}::test_deleted_user_is_dropped_unsent"),
    ("M10", CB, "            logger.error(\"Outbox forward rejected: %s status=%s\", what, resp.status_code)\n            return False",
     "            logger.error(\"Outbox forward rejected: %s status=%s\", what, resp.status_code)\n            return True",
     f"{T}::test_peer_rejection_keeps_the_row_and_logs_it"),
    ("M11", CB, "    if row[\"op_type\"] == USER_PROFILE_OP:", "    if False:",
     f"{T}::test_registration_queues_and_the_loop_sends_the_profile"),
    ("M12", CB, "        \"home_region\": user[\"home_region\"],", "        \"home_region\": node_region,",
     f"{T}::test_registration_queues_and_the_loop_sends_the_profile"),
    ("M13", CB, "        \"avatar_data\": user.get(\"avatar_data\") or \"\",", "        \"avatar_data\": \"\",",
     f"{T}::test_change_during_send_is_sent_next_round"),
    ("M14", MIG, "INSERT INTO cross_border_delete_outbox", "-- INSERT INTO cross_border_delete_outbox\nSELECT 1;\n--",
     f"{T}::test_backfill_migration_sends_every_local_user_once"),
    ("M15", MIG, "ON CONFLICT (op_type, target_id) DO NOTHING;",
     "ON CONFLICT (op_type, target_id) DO UPDATE SET payload = EXCLUDED.payload;",
     f"{T}::test_backfill_migration_keeps_a_pending_row_as_is"),
    ("M16", CB, "remove_delete_propagation(row[\"id\"], row.get(\"payload\"))",
     "remove_delete_propagation(row[\"id\"], \"\")",
     f"{T}::test_change_during_send_is_sent_next_round"),
    ("M17", CB, "    try:\n        headers = create_auth_header(body)\n",
     "    headers = create_auth_header(body)\n    try:\n",
     f"{T}::test_signing_failure_keeps_the_row_and_the_loop_alive"),
]

# M2 额外一步：入队挪到事务开始之前（自动提交），失败时不随事务回滚
M2_EXTRA = ("                async with conn.transaction():\n                    await conn.execute(\n                        \"INSERT INTO users",
            "                await _enqueue_profile_sync(conn, id)\n                async with conn.transaction():\n                    await conn.execute(\n                        \"INSERT INTO users")


def _swap(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{path}: 原文命中 {text.count(old)} 处，变异定位失效：{old[:60]!r}"
    p.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    survivors = []
    for mid, path, old, new, test in MUTANTS:
        originals = {f: Path(f).read_bytes() for f in (path, PG)}
        try:
            _swap(path, old, new)
            if mid == "M2":
                _swap(PG, *M2_EXTRA)
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test],
                               capture_output=True, text=True)
            red = r.returncode != 0
            print(f"{mid}: {'红（杀死）' if red else '绿（存活）'}  {test.split('::')[1]}")
            if not red:
                survivors.append(mid)
        finally:
            for f, b in originals.items():
                Path(f).write_bytes(b)
    print("存活：" + (", ".join(survivors) if survivors else "无"))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
