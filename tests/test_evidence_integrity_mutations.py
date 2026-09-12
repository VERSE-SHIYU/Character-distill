"""L1 —— 每条清单语义断言都必须有**自己的**专属红源（缺陷 14 v3 §三）。

为什么单独一个文件：`test_resume_numbers_is_current`（渲染新鲜度锁）是
`manifest → resume-numbers.md` 的逐字节比对，**任何**清单变异都会让它红。于是变异测试
失去鉴别力 —— 变异红了，但红的不是你以为的那条，看的人以为覆盖到位了。上一轮两个 gap
探针正是这样被它掩盖：重跑一次渲染就全绿。

本文件对每个变异**只跑指定的那一条**断言（进程内直接调用，不经 pytest 收集，因此不可能
被别的锁兜住），红则证明该命题有专属红源。

「新增断言必须补一行」这条约定**本身也是机器校验的**（见 `TestMutationCoverage`）——
它曾经只是本文件头部的一句散文：实测「新增断言不补行」与「删掉一行覆盖」都是全绿，
静默。散文约定 = 锁看不见 = 迟早漂移。豁免必须写进 `_NO_MUTATION_NEEDED` 并交代理由。

渲染新鲜度锁自己也占一行 —— 它的职责（渲染产物别过期）正当，但按上面的机制，得有人
证明它**同样**有专属红源，不是靠别的锁顶。
"""
from __future__ import annotations

import copy
import json
import re
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


def _launder_number_through_derived(entries):
    """spec v4 §三 的逃逸复验：claim 加一个凭空数字，再用 `derived` 给它编个来源。

    `refs` 写的是 `summary[0].out_tokens_p50` —— 那是**另一个条目**的 assertion path，
    不在 `incomplete-v5` 的 assertions 里，所以「从已绑定的量派生」这条当场不成立。
    claim 侧反而过得去（9999 被 derived 的 value 覆盖），这正是要证明的：**光有 claim 侧
    的覆盖不够**，`derived` 自己得先合法。
    """
    e = _by_id(entries, "incomplete-v5")
    e["claim"] += "；另有 9999 片"
    e["derived"].append(
        {"value": 9999, "formula": "x", "refs": ["summary[0].out_tokens_p50"]})


class _DummySemanticAssertion:
    """注入用的假类：反射能扫到、`MUTATIONS` 里没有对应行。

    元断言必须因此红 —— 否则证明它其实是靠硬编码类名过的（那就把 M1 要修的病往后挪了一层：
    新开的第五个类照样漏）。
    """

    def test_something_semantic(self):
        raise AssertionError("从不被执行 —— 它在差集里就够了")


def _drop_a_covering_row(_entries, mp):
    """删掉一行覆盖 → 元断言必须红。

    挑 `status_fourth_value`：它那个 nodeid **只**被这一行覆盖（其余 nodeid 都有第二行兜底），
    删掉它差集才非空。若将来 `MUTATIONS` 结构变了、这行不再是唯一覆盖，本条会先红 ——
    正是想要的信号：自指变异不能变成空过。
    """
    mp.setattr(sys.modules[__name__], "MUTATIONS",
               [r for r in MUTATIONS if r[0] != "status_fourth_value"])


def _exempt(prefix: str, reason: str, methods: list[str]) -> dict[str, str]:
    """把一组同因豁免展开成 `{nodeid: 理由}`。

    方法名仍**逐个列出**：新增方法照样落进差集、照样红。写成推导式扫全类就等于给
    「新增断言不表态」开了后门，与 `_NO_MUTATION_NEEDED` 要防的东西是同一个。
    """
    return {f"{prefix}::{m}": reason for m in methods}


# 不需要变异行的断言 —— **必须带理由**。写成 dict 而不是 list 就是为了这个：list 允许一行
# 字符串把断言悄悄豁免掉，dict 强制交代，下一个 reviewer 才能判断豁免是否成立。
_NO_MUTATION_NEEDED = {
    "TestReferenceClosure::test_the_scan_actually_reads_the_docs":
        "断言扫描面本身是活的（tracked .md 数量与关键文件在不在），与清单内容无关 —— "
        "变异清单不会也不该让它红",
    **_exempt("TestWriterContract",
              "出口行为测试：靶是 fixture 临时搬过去的 tmp 清单与 tmp 落点，"
              "与仓库清单内容无关 —— 变异仓库清单不会让它红",
              ["test_landing_zone_is_evidence_dir_and_id_named",
               "test_manifest_entry_is_verified_and_complete",
               "test_whitelist_drops_and_accounts_unlisted_keys",
               "test_upsert_replaces_same_id_instead_of_appending",
               "test_subset_may_narrow",
               "test_subset_may_not_widen",
               "test_unregistered_id_refused",
               "test_empty_whitelist_refused",
               "test_blank_manifest_fields_refused",
               "test_non_dict_payload_refused",
               "test_writer_exposes_no_path_or_whitelist_override",
               "test_landing_zone_is_not_env_overridable"]),
    **_exempt("TestRegisterArtifact",
              "迁移入口测试：同上（tmp 清单 + tmp 落点），与仓库清单内容无关",
              ["test_registers_existing_artifact_with_historical_date",
               "test_missing_artifact_refused",
               "test_register_requires_declarations_without_defaults",
               "test_bad_script_role_refused",
               "test_unregistered_id_refused"]),
}


def _assertion_nodeids() -> set[str]:
    """两个模块里**全部** `Test*` 类的 `test_*` —— 反射，不列举类名。

    列举类名 = 「新增断言」走不变量、「新增类」不走：将来有人新开第五个类，这里扫不到、
    元断言照旧绿，覆盖照样漏。两类新增必须走同一条不变量。

    两个模块都扫：M1 的元断言按设计住在本模块（它管的是矩阵自己），只扫 `lock` 会把它自己
    漏在差集之外 —— 那正是「覆盖检查自己有盲区」的重演。
    """
    out: set[str] = set()
    for mod in (lock, sys.modules[__name__]):
        for name, obj in vars(mod).items():
            if name.startswith("Test") and isinstance(obj, type):
                out |= {f"{name}::{attr}" for attr in dir(obj) if attr.startswith("test_")}
    return out


# (名字, 变异, 期望红的 nodeid)。nodeid 用 `Class::method`，在本文件内直接调用该方法。
MUTATIONS = [
    # —— M0 提取器反空过（TestExtractorPins）——
    ("path_corpus_loses_a_shape",
     lambda m, mp: mp.delitem(lock._PATH_CORPUS, "带行号后缀"),
     "TestExtractorPins::test_path_extractor_recognizes_the_pinned_shapes"),
    ("path_extractor_loses_a_shape",
     lambda m, mp: mp.setattr(
         lock, "_PATH_RE", re.compile(r"(?<=\s)(?:[\w.-]+/)*[\w.-]+\.py(?![\w])")),
     "TestExtractorPins::test_path_extractor_recognizes_the_pinned_shapes"),
    ("number_corpus_loses_a_shape",
     lambda m, mp: mp.delitem(lock._NUM_CORPUS, "斜杠分隔"),
     "TestExtractorPins::test_number_extractor_recognizes_the_pinned_shapes"),
    ("number_extractor_loses_a_shape",
     lambda m, mp: mp.setattr(
         lock, "_NUM_RE", re.compile(r"(?<![0-9A-Za-z_])\d{4,}(?![0-9A-Za-z_])")),
     "TestExtractorPins::test_number_extractor_recognizes_the_pinned_shapes"),
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
    ("claim_number_laundered_through_coined_derived",
     lambda m, mp: _launder_number_through_derived(m),
     "TestClaimBindings::test_derived_values_come_from_bound_refs"),
    ("derived_identity_value",
     lambda m, mp: _by_id(m, "thinking-maplen-after")["derived"].append(
         {"value": 2097, "formula": "复述 ref 自己", "refs": ["summary[1].out_tokens_max"]}),
     "TestClaimBindings::test_derived_values_come_from_bound_refs"),
    ("derived_ref_not_in_assertions",
     lambda m, mp: _by_id(m, "thinking-maplen-after")["derived"].append(
         {"value": 777, "formula": "随手一算", "refs": ["summary[9].out_tokens_p50"]}),
     "TestClaimBindings::test_derived_values_come_from_bound_refs"),
    ("derived_blank_formula",
     lambda m, mp: _by_id(m, "thinking-maplen-after")["derived"].append(
         {"value": 777, "formula": "   ", "refs": ["records"]}),
     "TestClaimBindings::test_derived_values_come_from_bound_refs"),
    ("derived_on_unverifiable",
     lambda m, mp: _by_id(m, "a2-wiring-mutation").update(
         derived=[{"value": 1, "formula": "x", "refs": ["a"]}]),
     "TestClaimBindings::test_derived_present_for_verified_and_null_otherwise"),
    ("derived_missing_on_verified",
     lambda m, mp: _by_id(m, "incomplete-v5").update(derived=None),
     "TestClaimBindings::test_derived_present_for_verified_and_null_otherwise"),
    ("assertions_on_unverifiable",
     lambda m, mp: _by_id(m, "graphify-snapshot-2026-08-15").update(
         assertions=[{"path": "a", "value": 1}]),
     "TestClaimBindings::test_assertions_present_for_verified_and_null_otherwise"),
    ("empty_assertions_on_verified",
     lambda m, mp: _by_id(m, "incomplete-v5").update(assertions=[]),
     "TestClaimBindings::test_assertions_present_for_verified_and_null_otherwise"),
    # —— M1 覆盖不变量（TestMutationCoverage）—— 元断言自己也进矩阵
    ("dummy_method_without_a_mutation_row",
     lambda m, mp: mp.setattr(
         lock.TestManifestEntries, "test_dummy_semantic", lambda self: None, raising=False),
     "TestMutationCoverage::test_every_semantic_assertion_has_a_mutation_row"),
    ("dummy_class_without_a_mutation_row",
     lambda m, mp: mp.setattr(lock, "TestInjectedDummy", _DummySemanticAssertion, raising=False),
     "TestMutationCoverage::test_every_semantic_assertion_has_a_mutation_row"),
    ("covering_row_deleted",
     _drop_a_covering_row,
     "TestMutationCoverage::test_every_semantic_assertion_has_a_mutation_row"),
]


class TestMutationCoverage:
    """M1 —— 「每个清单语义断言都有专属红源」本身必须是机器校验的，不是文件头的一句散文。

    实测过的两个静默逃逸：新增一条语义断言而不补行 → 62 passed；删掉一行覆盖 → 60 passed。
    散文约定锁看不见，于是这条不变量一直没有红源。
    """

    def test_every_semantic_assertion_has_a_mutation_row(self):
        all_ids = _assertion_nodeids()
        covered = {row[2] for row in MUTATIONS}
        missing = sorted(all_ids - set(_NO_MUTATION_NEEDED) - covered)
        assert not missing, (
            "这些断言语义上属于清单，却没有专属变异行 —— 它的绿可能是被渲染新鲜度锁掩盖的："
            f"{missing}。补一行 MUTATIONS，或加进 _NO_MUTATION_NEEDED 并写明理由。")
        unknown = sorted(covered - all_ids)
        assert not unknown, f"MUTATIONS 指向了不存在的断言：{unknown}"
        stale = sorted(set(_NO_MUTATION_NEEDED) - all_ids)
        assert not stale, f"_NO_MUTATION_NEEDED 里的豁免指向已不存在的断言：{stale}"


def _target_method(nodeid: str):
    """按 nodeid 取断言方法 —— 先在锁模块里找，再在本模块里找。

    靶不总在 `lock` 里：M1 的元断言按设计住在本模块（它管的是矩阵自己）。
    """
    cls_name, method_name = nodeid.split("::")
    for mod in (lock, sys.modules[__name__]):
        cls = getattr(mod, cls_name, None)
        if isinstance(cls, type):
            return getattr(cls(), method_name)
    raise LookupError(f"没有哪个模块定义了 {cls_name}")


@pytest.mark.parametrize("name,mutate,nodeid", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_reds_its_own_assertion(tmp_path, monkeypatch, name, mutate, nodeid):
    """只跑目标那一条：它必须**自己**红。别的锁红不红与本行无关（spec 允许）。"""
    entries = copy.deepcopy(json.loads(lock.MANIFEST.read_text(encoding="utf-8")))
    mutate(entries, monkeypatch)
    mutated = tmp_path / "manifest.json"
    mutated.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(lock, "MANIFEST", mutated)

    with pytest.raises(AssertionError):
        _target_method(nodeid)()
