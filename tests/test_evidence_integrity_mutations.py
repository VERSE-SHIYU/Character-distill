"""L1 —— 每条清单语义断言都必须有**自己的**专属红源（缺陷 14 v3 §三）。

为什么单独一个文件：`test_resume_numbers_is_current`（渲染新鲜度锁）是
`manifest → resume-numbers.md` 的逐字节比对，**任何**清单变异都会让它红。于是变异测试
失去鉴别力 —— 变异红了，但红的不是你以为的那条，看的人以为覆盖到位了。上一轮两个 gap
探针正是这样被它掩盖：重跑一次渲染就全绿。

本文件对每个变异**只跑指定的那一条**断言（进程内直接调用，不经 pytest 收集，因此不可能
被别的锁兜住），红则证明该命题有专属红源。

硬要求（spec §三）：此后新增任何清单语义断言，必须同时在本文件 `MUTATIONS` 补一行。
不补 = 该断言可能只被渲染锁掩盖，而作者会以为有覆盖。

渲染新鲜度锁自己也占一行 —— 它的职责（渲染产物别过期）正当，但按上面的机制，得有人
证明它**同样**有专属红源，不是靠别的锁顶。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "perf"))

import test_evidence_integrity as lock  # noqa: E402
import evidence_writer  # noqa: E402


def _by_id(entries: list[dict], evidence_id: str) -> dict:
    return next(e for e in entries if e["id"] == evidence_id)


def _add_ghost_entry(entries, _mp):
    entries.append({
        "id": "ghost-unregistered", "claim": "c", "status": "runtime-measured",
        "artifact": None, "script": None, "script_role": None, "reproduce": None,
        "env": "e", "measured_at": "2026-01-01", "code_sha": "deadbee",
        "redacted_fields": [], "notes": "n",
    })


def _drop_whitelist(entries, mp):
    mp.delitem(evidence_writer._ALLOWED_BY_ID, "incomplete-v5")


# (名字, 变异, 期望红的 nodeid)。nodeid 用 `Class::method`，在本文件内直接调用该方法。
MUTATIONS = [
    # —— 条目形状断言（TestManifestEntries）——
    ("status_fourth_value",
     lambda m, mp: _by_id(m, "incomplete-v5").update(status="rejected"),
     "TestManifestEntries::test_status_is_exactly_one_of_three"),
    ("blank_claim",
     lambda m, mp: _by_id(m, "incomplete-v5").update(claim="   "),
     "TestManifestEntries::test_required_fields_present_and_non_empty"),
    ("ghost_artifact",
     lambda m, mp: _by_id(m, "incomplete-v5").update(artifact="docs/evidence/ghost.json"),
     "TestManifestEntries::test_verified_artifact_is_git_tracked"),
    ("unverifiable_carrying_artifact",
     lambda m, mp: _by_id(m, "graphify-snapshot-2026-08-15").update(
         artifact="docs/evidence/incomplete-v5.json"),
     "TestManifestEntries::test_other_statuses_carry_no_artifact"),
    ("artifact_outside_evidence_dir",
     lambda m, mp: _by_id(m, "incomplete-v5").update(artifact="docs/incomplete-v5.json"),
     "TestManifestEntries::test_artifact_lives_under_docs_evidence"),
    ("illegal_script_role_on_non_verified",
     lambda m, mp: _by_id(m, "a2-wiring-mutation").update(script_role="producer"),
     "TestManifestEntries::test_script_role_is_declared_and_legal"),
    ("corroborating_without_notes",
     lambda m, mp: _by_id(m, "incomplete-v5").update(notes=""),
     "TestManifestEntries::test_corroborating_script_must_be_explained"),
    ("manifest_entry_unregistered_whitelist",
     _add_ghost_entry,
     "TestManifestEntries::test_every_entry_has_a_registered_whitelist"),
    # —— L2 指称闭合（TestManifestEntries）——
    ("ghost_script",
     lambda m, mp: _by_id(m, "incomplete-v5").update(script="tests/perf/ghost_probe.py"),
     "TestManifestEntries::test_script_is_tracked"),
    ("ghost_path_in_reproduce",
     lambda m, mp: _by_id(m, "incomplete-v5").update(
         reproduce="python tests/perf/ghost_probe.py"),
     "TestManifestEntries::test_reproduce_paths_resolve"),
    ("fabricated_code_sha",
     lambda m, mp: _by_id(m, "incomplete-v5").update(code_sha="deadbee"),
     "TestManifestEntries::test_code_sha_resolves"),
    ("ghost_path_in_notes",
     lambda m, mp: _by_id(m, "a2-wiring-mutation").update(notes="见 tests/perf/ghost_probe.py"),
     "TestManifestEntries::test_notes_and_claim_paths_resolve"),
    ("gitignored_path_without_marker",
     lambda m, mp: _by_id(m, "a2-wiring-mutation").update(notes="见 `.claude/sessions/x.md`"),
     "TestManifestEntries::test_notes_and_claim_paths_resolve"),
    # —— 正文引用闭合（TestReferenceClosure）——
    ("doc_ref_loses_its_entry",
     lambda m, mp: m.remove(_by_id(m, "incomplete-v5")),
     "TestReferenceClosure::test_every_reference_resolves_to_a_manifest_entry"),
    ("referenced_id_loses_its_whitelist",
     _drop_whitelist,
     "TestReferenceClosure::test_every_reference_has_a_registered_whitelist"),
    # —— 渲染新鲜度（TestRenderedResumeList）—— 它自己也得有专属红源
    ("measured_at_drifts_from_render",
     lambda m, mp: _by_id(m, "incomplete-v5").update(measured_at="2020-01-01"),
     "TestRenderedResumeList::test_resume_numbers_is_current"),
    # —— L3 数值闭合（TestClaimBindings）——
    ("assertion_value_off_by_one",
     lambda m, mp: _by_id(m, "incomplete-v5")["assertions"][0].update(value=7),
     "TestClaimBindings::test_assertion_values_match_artifact"),
    ("assertion_path_typo",
     lambda m, mp: _by_id(m, "incomplete-v5")["assertions"][0].update(path="totalChunksX"),
     "TestClaimBindings::test_assertion_values_match_artifact"),
    ("unbound_claim_number",
     lambda m, mp: _by_id(m, "incomplete-v5").update(
         claim=_by_id(m, "incomplete-v5")["claim"] + "；另有 9999 片"),
     "TestClaimBindings::test_claim_numbers_are_bound_or_declared_derived"),
    ("derived_block_without_algorithm",
     lambda m, mp: _by_id(m, "incomplete-v5").update(notes="派生量：随便写写"),
     "TestClaimBindings::test_claim_numbers_are_bound_or_declared_derived"),
    ("assertions_on_unverifiable",
     lambda m, mp: _by_id(m, "graphify-snapshot-2026-08-15").update(
         assertions=[{"path": "a", "value": 1}]),
     "TestClaimBindings::test_assertions_present_for_verified_and_null_otherwise"),
    ("empty_assertions_on_verified",
     lambda m, mp: _by_id(m, "incomplete-v5").update(assertions=[]),
     "TestClaimBindings::test_assertions_present_for_verified_and_null_otherwise"),
]


@pytest.mark.parametrize("name,mutate,nodeid", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_reds_its_own_assertion(tmp_path, monkeypatch, name, mutate, nodeid):
    """只跑目标那一条：它必须**自己**红。别的锁红不红与本行无关（spec 允许）。"""
    entries = copy.deepcopy(json.loads(lock.MANIFEST.read_text(encoding="utf-8")))
    mutate(entries, monkeypatch)
    mutated = tmp_path / "manifest.json"
    mutated.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(lock, "MANIFEST", mutated)

    cls_name, method_name = nodeid.split("::")
    method = getattr(getattr(lock, cls_name)(), method_name)
    with pytest.raises(AssertionError):
        method()
