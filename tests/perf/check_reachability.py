"""核对探针产物：每条用例命中的 raise 站点，是否就是它声称要验的那个属主 guard。

用法：
    python e2e/scratch/check_reachability.py e2e/scratch/raise_sites.json [更多.json...]

判定：
  - 32 条 A 类：必须恰好命中 1 个 raise，status==404，且 site 的函数名 == 期望的 handler
  - 3 条 B 类：必须命中 status==403，且函数名 == 期望
  - 其余（文案一致性）：必须命中 2 个 raise（非属主 + 不存在），status 均 404，函数名一致
任何一条不满足即红并列出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 用例短名 → (期望函数名, 期望状态码)
EXPECTED: dict[str, tuple[str, int]] = {
    # card
    "TestCardOwnership::test_get_card_avatar_404": ("get_card_avatar", 404),
    "TestCardOwnership::test_save_card_avatar_404": ("save_card_avatar", 404),
    "TestCardOwnership::test_export_card_404": ("export_card", 404),
    "TestCardOwnership::test_get_card_404": ("get_card", 404),
    # distill
    "TestDistillOwnership::test_task_status_404": ("distill_task_status", 404),
    "TestDistillOwnership::test_cancel_task_404": ("cancel_distill_task", 404),
    "TestDistillOwnership::test_task_params_404": ("distill_task_params", 404),
    "TestDistillOwnership::test_update_card_404": ("update_card", 404),
    "TestDistillOwnership::test_export_card_404": ("export_card", 404),
    # group
    "TestGroupOwnership::test_create_group_with_foreign_card_404": ("create_group", 404),
    "TestGroupOwnership::test_list_affinities_404": ("list_group_affinities", 404),
    "TestGroupOwnership::test_toggle_reaction_404": ("toggle_reaction", 404),
    "TestGroupOwnership::test_get_history_404": ("get_history", 404),
    "TestGroupOwnership::test_rename_group_404": ("rename_group", 404),
    # market
    "TestMarketOwnership::test_publish_card_404": ("publish_card", 404),
    "TestMarketOwnership::test_update_published_card_404": ("update_published_card", 404),
    "TestMarketOwnership::test_update_card_version_404": ("update_card_version", 404),
    "TestMarketOwnership::test_delete_market_card_404": ("delete_market_card", 404),
    "TestMarketOwnership::test_batch_delete_comments_404": ("batch_delete_comments", 404),
    "TestMarketOwnership::test_delete_foreign_comment_404": ("delete_comment", 404),
    "TestMarketOwnership::test_set_visibility_404": ("set_visibility", 404),
    # memory
    "TestMemoryOwnership::test_list_memories_404": ("list_memories", 404),
    "TestMemoryOwnership::test_add_memory_404": ("add_memory", 404),
    "TestMemoryOwnership::test_update_memory_404": ("update_memory", 404),
    "TestMemoryOwnership::test_delete_memory_404": ("delete_memory", 404),
    "TestMemoryOwnership::test_clear_memories_404": ("clear_memories", 404),
    # message
    "TestMessageOwnership::test_react_to_foreign_dm_404": ("react_to_dm", 404),
    "TestMessageOwnership::test_retract_still_403": ("retract_dm_message", 403),
    # voice
    "TestVoiceOwnership::test_delete_foreign_custom_voice_404": ("delete_custom_voice", 404),
    "TestVoiceOwnership::test_preview_ref_audio_404": ("preview_ref_audio", 404),
    "TestVoiceOwnership::test_get_ref_audio_404": ("get_ref_audio", 404),
    "TestVoiceOwnership::test_upload_ref_audio_404": ("upload_ref_audio", 404),
    "TestVoiceOwnership::test_delete_ref_audio_404": ("delete_ref_audio", 404),
    # B 权限型：必须仍是 403 且来自各自的门
    "TestPermission403StillCoversAdminOnly::test_market_delete_version_non_admin_403":
        ("delete_card_version", 403),
    "TestPermission403StillCoversAdminOnly::test_admin_api_non_admin_403":
        ("require_admin", 403),
}

# 文案一致性用例：两侧必须命中同一个函数、同一状态码
PARITY = {
    "TestMessageParity::test_card_parity": ("get_card", 404),
    "TestMessageParity::test_group_history_parity": ("get_history", 404),
    "TestMessageParity::test_distill_task_parity": ("distill_task_status", 404),
    "TestMessageParity::test_market_visibility_parity": ("set_visibility", 404),
    "TestMessageParity::test_memory_parity": ("list_memories", 404),
    "TestMessageParity::test_voice_ref_audio_parity": ("get_ref_audio", 404),
    "TestMessageParity::test_dm_react_parity": ("react_to_dm", 404),
}


def func_of(site: str | None) -> str:
    return site.rsplit(":", 1)[-1] if site else "<none>"


def check(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    recs = {k.split("::", 1)[1] if k.startswith("tests/") else k: v
            for k, v in data["records"].items()}
    # nodeid 形如 tests/test_ownership_404.py::Class::test → 取其后半
    recs = {}
    for k, v in data["records"].items():
        parts = k.split("::")
        recs["::".join(parts[1:])] = v

    bad: list[str] = []
    print(f"\n=== {path}  (no_key_mode={data['no_key_mode']}) ===")
    print(f"{'case':<58} {'exp':>4} {'got':>4}  site")
    for name, (want_func, want_status) in EXPECTED.items():
        got = recs.get(name, [])
        if len(got) != 1:
            bad.append(f"{name}: 命中 {len(got)} 个 raise（期望 1）")
            print(f"{name:<58} {want_status:>4} {'--':>4}  <{len(got)} raises>")
            continue
        g = got[0]
        ok = g["status"] == want_status and func_of(g["site"]) == want_func
        if not ok:
            bad.append(f"{name}: 期望 {want_status}@{want_func}，实际 {g['status']}@{g['site']}")
        print(f"{name:<58} {want_status:>4} {g['status']:>4}  {g['site']}")

    for name, (want_func, want_status) in PARITY.items():
        got = recs.get(name, [])
        funcs = {func_of(g["site"]) for g in got}
        statuses = {g["status"] for g in got}
        ok = len(got) == 2 and funcs == {want_func} and statuses == {want_status}
        if not ok:
            bad.append(f"{name}: 期望 2×{want_status}@{want_func}，实际 {sorted(statuses)}@{sorted(funcs)}")
        print(f"{name:<58} {'2x' + str(want_status):>4} {len(got):>4}  {sorted(funcs)}")

    print("-" * 100)
    if bad:
        print(f"失败 {len(bad)} 条：")
        for b in bad:
            print("  -", b)
    else:
        print(f"全数通过：A 类 {len(EXPECTED) - 3} 条 + B 类 3 条 + 文案 {len(PARITY)} 条"
              f" 都命中了预期的 raise 站点")
    return 1 if bad else 0


if __name__ == "__main__":
    rc = 0
    for p in sys.argv[1:] or ["e2e/scratch/raise_sites.json"]:
        rc |= check(p)
    sys.exit(rc)
