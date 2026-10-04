# -*- coding: utf-8 -*-
"""F 系列：共享变异执行框架的判别器（spec `card-draft-mutation-framework.md` §7）。

用**合成输入**（可调用靶子 + `tmp_path` 里的小域文件）判每条决定，**不起子进程** —— 框架
的每一条行为（汇总行取法、红源收 `E ` 行、异常时还原、空转/编号重复/标记/skip/期望值/
可调用靶子/预筛/跑不起来/收尾核 sha256）都在这一层被单独钉住；四个旧驱动的**行为不变**由
S1/S3 的机器比对守（F16），不在这里。
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

import lock_coverage

PERF = pathlib.Path(__file__).resolve().parent / "perf"
sys.path.insert(0, str(PERF))

import mutation_framework as F  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把框架的 `ROOT` 指到临时树（`_apply` 用它算 `_TOUCHED` 的相对路径），并清共享态。"""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    F._TOUCHED.clear()
    F._CREATED.clear()
    yield tmp_path
    F._TOUCHED.clear()
    F._CREATED.clear()


def _domain_file(tmp_path, name="lock.py"):
    p = tmp_path / name
    p.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    return p


def _a_domain_line(p):
    lines = sorted(lock_coverage.discriminators(p))
    assert lines, "合成域文件里没有判别器 —— 测试前提不成立"
    return lines[0]


def _callable(got, *, keep=(), lines=(), problems=()):
    def target():
        return got, list(keep), set(lines), list(problems)
    return target


def _matrix(tmp_path, items, *, domain, targets=(), artifact="art.json", **kw):
    art = tmp_path / artifact
    rc = F.run_matrix(items, domain=domain, targets=targets, artifact=art,
                      driver_rel="tests/perf/fake_mutations.py", root=tmp_path, **kw)
    return rc, art


# ── F1 / F2：汇总行与红源摘要的取法 ───────────────────────────────────────────

def test_summary_is_the_last_matching_line():
    """M34 的真实输出片段：失败用例的 traceback 里也有「… error … in …」，别把它当汇总行。"""
    out = (
        "tests/test_card_draft.py:305: in test_validate_rejects_unknown_points\n"
        '    assert any(isinstance(p, dict) and "error" in p for p in payload)\n'
        "E   AssertionError: \n"
        "tests/test_card_draft.py:309: AssertionError\n"
        "FAILED tests/test_card_draft.py::test_validate_rejects_unknown_points\n"
        "1 failed, 23 passed, 1 warning in 4.19s\n"
    )
    assert F.summary_of(out) == "1 failed, 23 passed, 1 warning in 4.19s"


def test_keep_includes_exception_lines(sandbox, monkeypatch):
    """非断言失败在 FAILED 行里不带异常原文 —— 红源摘要必须收 `E ` 行，标记才匹配得到。"""
    class R:
        stdout = ("tests/x.py:5: in test_y\n"
                  "    f()\n"
                  "E   RuntimeError: boom\n"
                  "1 failed in 0.10s\n")
        stderr = ""
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    _summary, keep, _lines, _problems = F._run("tests/x.py")
    assert any(k.startswith("E ") for k in keep), keep


# ── F3 / F4：还原（异常时也还原，新建文件收尾删除，write 只许新建）──────────────

def test_targets_are_restored_when_a_target_raises(sandbox, tmp_path):
    tgt = tmp_path / "mut.py"
    tgt.write_text("x = 1\n", encoding="utf-8")

    def boom():
        raise RuntimeError("靶子执行中抛异常")

    items = [("M1", boom, [("append", tgt, "\n# mutated\n")], "RED")]
    with pytest.raises(RuntimeError):
        _matrix(tmp_path, items, domain=["mut.py"], targets=[tgt])
    assert tgt.read_text(encoding="utf-8") == "x = 1\n"


def test_written_file_is_removed_on_restore(sandbox, tmp_path):
    created = tmp_path / "created.py"
    dom = _domain_file(tmp_path)
    loc = f"{dom.name}:{_a_domain_line(dom)}"
    items = [("M1", _callable("RED", lines={loc}), [("write", created, "y = 1\n")], "RED")]
    rc, _art = _matrix(tmp_path, items, domain=[dom.name], targets=[dom])
    assert rc == 0
    assert not created.exists()


def test_write_refuses_an_existing_path(sandbox, tmp_path):
    existing = tmp_path / "e.py"
    existing.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        F._apply([("write", existing, "y = 1\n")])


# ── F5 / F6：空转与编号重复 ──────────────────────────────────────────────────

def test_vacuous_red_is_a_mismatch_and_writes_no_artifact(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    items = [("MX", _callable("RED"), [], "RED")]     # 红了，但红源不在覆盖域里
    rc, art = _matrix(tmp_path, items, domain=[dom.name], targets=[dom])
    assert rc == 1
    assert not art.exists()


def test_duplicate_labels_are_refused(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    loc = f"{dom.name}:{_a_domain_line(dom)}"
    items = [("M1", _callable("RED", lines={loc}), [], "RED"),
             ("M1", _callable("RED", lines={loc}), [], "RED")]
    with pytest.raises(AssertionError):
        _matrix(tmp_path, items, domain=[dom.name], targets=[dom])


# ── F7 / F8 / F9：标记、skip、期望值归类 ─────────────────────────────────────

def test_marker_accepts_a_string_or_a_sequence(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    loc = f"{dom.name}:{_a_domain_line(dom)}"

    ok = ("M1", _callable("RED", keep=["boom here"], lines={loc}), [], "RED", "boom")
    rc_ok, _ = _matrix(tmp_path, [ok], domain=[dom.name], targets=[dom])
    assert rc_ok == 0

    bad = ("M2", _callable("RED", keep=["boom"], lines={loc}), [], "RED", ("boom", "nope"))
    rc_bad, _ = _matrix(tmp_path, [bad], domain=[dom.name], targets=[dom])
    assert rc_bad == 1     # 一串里缺一个 → mismatch


def test_registered_skip_goes_to_skipped(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    item = ("B-1", _callable("本环境不适用"), [], "RED")
    rc, art = _matrix(tmp_path, [item], domain=[dom.name], targets=[dom], may_skip={"B-1"})
    assert rc == 0
    assert json.loads(art.read_text(encoding="utf-8"))["skipped"] == ["B-1"]


def test_unregistered_skip_is_a_mismatch(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    item = ("B-9", _callable("本环境不适用"), [], "RED")
    rc, art = _matrix(tmp_path, [item], domain=[dom.name], targets=[dom])
    assert rc == 1
    assert not art.exists()


def test_expected_green_and_ok_go_to_controls(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    items = [("G1", _callable("green"), [], "green"),
             ("O1", _callable("OK"), [], "OK")]
    rc, art = _matrix(tmp_path, items, domain=[dom.name], targets=[dom])
    assert rc == 0
    assert json.loads(art.read_text(encoding="utf-8"))["controls"] == ["G1", "O1"]


# ── F10 / F11 / F12 / F13：可调用靶子、预筛、跑不起来、收尾核 sha256 ───────────

def test_callable_target_verdict_is_used_as_is(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    # 若框架对可调用靶子也走 `outcome("OK")`（→ green），期望 OK 就会 mismatch。
    item = ("I-3", _callable("OK"), [], "OK")
    rc, _art = _matrix(tmp_path, [item], domain=[dom.name], targets=[dom])
    assert rc == 0


def test_pre_skipped_labels_land_in_the_artifact(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    loc = f"{dom.name}:{_a_domain_line(dom)}"
    item = ("M1", _callable("RED", lines={loc}), [], "RED")
    rc, art = _matrix(tmp_path, [item], domain=[dom.name], targets=[dom],
                      pre_skipped=("X-1", "X-2"))
    assert rc == 0
    assert json.loads(art.read_text(encoding="utf-8"))["skipped"] == ["X-1", "X-2"]


def test_runaway_is_a_mismatch(sandbox, tmp_path):
    dom = _domain_file(tmp_path)
    item = ("M1", _callable("跑不出来"), [], "RED")
    rc, art = _matrix(tmp_path, [item], domain=[dom.name], targets=[dom])
    assert rc == 1
    assert not art.exists()


def test_unrestored_target_is_a_mismatch(sandbox, tmp_path, monkeypatch):
    tgt = tmp_path / "mut.py"
    tgt.write_text("x = 1\n", encoding="utf-8")
    dom = _domain_file(tmp_path)
    loc = f"{dom.name}:{_a_domain_line(dom)}"
    monkeypatch.setattr(F, "_restore", lambda baseline: None)     # 模拟还原失败
    # 红源落进域里（不空转）—— 否则空转守卫也会记一条 mismatch，这条对「收尾核 sha256」
    # 就没了分辨力（删掉 sha 比对后仍 rc==1，判据打不红）。
    item = ("M1", _callable("RED", lines={loc}), [("append", tgt, "\n# m\n")], "RED")
    rc, _art = _matrix(tmp_path, [item], domain=[dom.name], targets=[tgt])
    assert rc == 1


# ── F14 / F15：执行原语只有一份；alerting 的域文件门 ──────────────────────────

def test_drivers_define_no_execution_primitives():
    """执行原语只许在 `mutation_framework.py` 一份；每个驱动都必须调 `run_matrix`。"""
    primitives = {"_hidden", "_apply", "_restore", "_run", "_run_py"}
    drivers = sorted(PERF.glob("*_mutations.py"))
    assert drivers, "没找到任何变异驱动"
    for driver in drivers:
        tree = ast.parse(driver.read_text(encoding="utf-8"))
        defined = {n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        dup = defined & primitives
        assert not dup, f"{driver.name} 自己定义了执行原语 {sorted(dup)} —— 只许有框架一份"
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    called.add(node.func.id)
        assert "run_matrix" in called, f"{driver.name} 没有调用 run_matrix"


def test_alerting_refuses_edits_on_its_domain():
    """alerting 的变异不许落在覆盖域的靶子文件上（行号会平移，红源坐标当场作废）。"""
    import alerting_mutations as A

    fake = ("X 打域文件", "tests/test_failure_alerting.py",
            [("append", A.TEST, "\n")], "RED")
    assert A._domain_edits([fake]) == ["X 打域文件"]
    assert A._domain_edits(A.MUTATIONS) == []      # 现表一条都不碰域文件
