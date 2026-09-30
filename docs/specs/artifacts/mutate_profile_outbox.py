"""变异预跑（spec profile-outbox 对账表 M1–M17）：逐条把改后代码改坏一处，对应测试必须红
在**指定的那句断言**上；跑完逐字节还原。

用法：在仓库根目录 `python docs/specs/artifacts/mutate_profile_outbox.py`
（需 TEST_DATABASE_URL 指向测试 PG）。

**执行框架与判档不在本文件里**：改文件 / 跑 pytest / 还原用 `tests/perf/route_facts_mutations.py`
（`_apply` 锚点恰一命中、`_run` 取汇总行与断言行、`_restore`），判档与基线门用
`tests/lock_coverage.py`（`outcome` / `baseline_verdict` / `refuse_on_baseline`）—— 与仓内
三个变异驱动共用同一份实现。本文件只放 spec 的变异表。

它**不**登记进覆盖闭合元锁（不写 `*_red_lines.json`、不放 `tests/perf/`）：这是一份 spec 的
一次性对账，不是常设守卫。

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

LOCK = "tests/test_profile_outbox.py"
PG = ROOT / "storage/postgres_store.py"
CB = ROOT / "web/cross_border_sync.py"
MIG = ROOT / "storage/migrations_pg/030_backfill_profile_sync.sql"
TARGETS = (PG, CB, MIG)


def _t(name: str) -> str:
    return f"{LOCK}::{name}"


# (编号, 靶子测试, [(动作, 文件, 载荷)], 红源标记 —— 必须出现在断言行里)
MUTANTS = [
    ("M1 注册不入队", _t("test_registration_queues_and_the_loop_sends_the_profile"),
     [("repl", PG, [("                    await _enqueue_profile_sync(conn, id)\n", "")])],
     "注册没有把资料入队"),
    ("M2 入队挪到事务外", _t("test_failed_create_user_leaves_no_profile_row"),
     [("repl", PG, [
         ("                    await _enqueue_profile_sync(conn, id)\n                return",
          "                return"),
         ("                async with conn.transaction():\n                    await conn.execute(\n"
          "                        \"INSERT INTO users",
          "                await _enqueue_profile_sync(conn, id)\n"
          "                async with conn.transaction():\n                    await conn.execute(\n"
          "                        \"INSERT INTO users"),
     ])],
     "建用户失败却留下了待发行"),
    ("M3 资料变更不换版本戳", _t("test_change_during_send_is_sent_next_round"),
     [("repl", PG, [("ON CONFLICT (op_type, target_id) DO UPDATE SET payload = EXCLUDED.payload\"\"\",\n"
                     "        USER_PROFILE_OP",
                     "ON CONFLICT (op_type, target_id) DO NOTHING\"\"\",\n        USER_PROFILE_OP")])],
     "发送途中的新资料被这一轮的确认一起删掉了"),
    ("M4 改头像不入队", _t("test_avatar_change_requeues_and_unknown_user_does_not"),
     [("repl", PG, [("                    if self._parse_rowcount(status):\n"
                     "                        await _enqueue_profile_sync(conn, user_id)\n", "")])],
     "资料变了却没换版本戳"),
    ("M5 不存在的用户也入队", _t("test_avatar_change_requeues_and_unknown_user_does_not"),
     [("repl", PG, [("                    if self._parse_rowcount(status):\n",
                     "                    if True:\n")])],
     "不存在的用户不该入队"),
    ("M6 确认后按 id 删", _t("test_change_during_send_is_sent_next_round"),
     [("repl", PG, [("WHERE id = $1 AND payload IS NOT DISTINCT FROM $2\",\n                    id, payload,",
                     "WHERE id = $1\",\n                    id,")])],
     "发送途中的新资料被这一轮的确认一起删掉了"),
    ("M7 NULL payload 删不掉", _t("test_acked_row_with_null_payload_is_removed"),
     [("repl", PG, [("payload IS NOT DISTINCT FROM $2", "payload = $2")])],
     "确认过的 NULL payload 行没删掉"),
    ("M8 不判地区", _t("test_user_not_homed_here_is_dropped_unsent"),
     [("repl", CB, [("    if user.get(\"home_region\") != node_region():", "    if False:")])],
     "非本区用户被本节点宣告出去了"),
    ("M9 已删用户留行", _t("test_deleted_user_is_dropped_unsent"),
     [("repl", CB, [("        logger.info(\"Outbox profile dropped, user no longer exists: %s\", what)\n"
                     "        return True",
                     "        logger.info(\"Outbox profile dropped, user no longer exists: %s\", what)\n"
                     "        return False")])],
     "用户已不存在，这一行永远发不出去"),
    ("M10 非 200 当成功", _t("test_peer_rejection_keeps_the_row_and_logs_it"),
     [("repl", CB, [("            logger.error(\"Outbox forward rejected: %s status=%s\", what, resp.status_code)\n"
                     "            return False",
                     "            logger.error(\"Outbox forward rejected: %s status=%s\", what, resp.status_code)\n"
                     "            return True")])],
     "对端没确认，这一行不能丢"),
    ("M11 资料行走删除分支", _t("test_registration_queues_and_the_loop_sends_the_profile"),
     [("repl", CB, [("    if row[\"op_type\"] == USER_PROFILE_OP:", "    if False:")])],
     "资料没发出去"),
    ("M12 发函数当地区（原缺陷）", _t("test_registration_queues_and_the_loop_sends_the_profile"),
     [("repl", CB, [("        \"home_region\": user[\"home_region\"],", "        \"home_region\": node_region,")])],
     "资料没发出去"),
    ("M13 头像写死空串", _t("test_change_during_send_is_sent_next_round"),
     [("repl", CB, [("        \"avatar_data\": user.get(\"avatar_data\") or \"\",", "        \"avatar_data\": \"\",")])],
     "对端没收到最新头像"),
    ("M14 迁移体为空", _t("test_backfill_migration_sends_every_local_user_once"),
     [("write", MIG, "-- 变异：迁移体为空\nSELECT 1;\n")],
     "没被 030 入队"),
    ("M15 迁移改动已有待发行", _t("test_backfill_migration_keeps_a_pending_row_as_is"),
     [("repl", MIG, [("ON CONFLICT (op_type, target_id) DO NOTHING;",
                      "ON CONFLICT (op_type, target_id) DO UPDATE SET payload = EXCLUDED.payload;")])],
     "030 改动了已有的待发行"),
    ("M16 删行不传发出时的戳", _t("test_change_during_send_is_sent_next_round"),
     [("repl", CB, [("remove_delete_propagation(row[\"id\"], row.get(\"payload\"))",
                     "remove_delete_propagation(row[\"id\"], \"\")")])],
     "按发出时的版本戳删不掉已确认的行"),
    ("M17 签名挪出 try", _t("test_signing_failure_keeps_the_row_and_the_loop_alive"),
     [("repl", CB, [("    try:\n        headers = create_auth_header(body)\n",
                     "    headers = create_auth_header(body)\n    try:\n")])],
     "签名失败把补发循环带停了"),
]


def main() -> int:
    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    summary, _, _, _ = framework._run(LOCK)
    print(f"  {LOCK}  {summary}")
    cause = lock_coverage.baseline_verdict(summary)
    if cause:
        return lock_coverage.refuse_on_baseline({LOCK: cause})

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
