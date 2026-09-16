# -*- coding: utf-8 -*-
"""元锁：**每条判别器都有专属变异撞过** —— 判别器集合与「变异实际红在哪一行」的集合相等。

**它防的是什么。** 判别器与变异之间本来没有闭合关系：锁里有 N 条判别器、矩阵里有 M 条
变异，N ≠ M 时**没有任何东西会发现**。缺陷 41 的 C 轮就是这样 —— 24 条旧变异里 11 条
没了对应，其中 8 条「判别器还在、只是再没人撞它」，是靠人手工对表才发现的。手工对表既
查不全（只对了包装类那 24 个调用点，`assert` 与 `raise` 两类从没对过），也留不下守卫。

**与缺陷 21/23 同型。** 21 是「迁移文件 ↔ 执行清单无闭合」→ 建目录求差锁；23 是「声明表
↔ 真库无闭合」→ 建真库 ⊇ 声明锁；本条是「判别器 ↔ 变异无闭合」→ 两个集合相等。

**集合相等为什么不是过严的要求。** E 是**所有变异红到的行的并集**，一条变异可以同时撞到
多条判别器（它一红，整条调用链上的判别器都会进 E）。所以「每条判别器都被撞过」不需要
「每条判别器一条变异」，只需要变异能覆盖到它。

**递归在这里停：** 本文件自己也是锁，但它**不在覆盖域里** —— 域取自各变异驱动的事实字段
（驱动表里每条变异的靶子文件）。今天没有驱动把 `test_lock_coverage.py` 列为靶子，所以它
天生不在域内。这不是豁免：将来谁真给元锁写了驱动，它自动进域。判据自身推出终止，不是
人为规定。本文件的有效性由下面三条合成输入用例承担（**不设豁免名单**的道理见
`lock_coverage` 的模块 docstring）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

import lock_coverage

ROOT = pathlib.Path(__file__).resolve().parents[1]
PERF = ROOT / "tests" / "perf"


def _artifacts() -> list[pathlib.Path]:
    return sorted(PERF.glob(f"*{lock_coverage.ARTIFACT_SUFFIX}"))


# ── 闭包（缺陷 21 同型）：有驱动就得有产物，有产物就得有驱动 ────────────────────

_MUT_SUFFIX = "_mutations.py"


def test_every_mutation_driver_has_an_artifact_and_vice_versa():
    """驱动 `x_mutations.py` ↔ 产物 `x_red_lines.json`：两边各自**去掉自己的尾巴**后必须相等。

    **两边去的是不同的尾巴，这是本条唯一容易写错的地方。** 产物名里没有 `_mutations`、驱动名里
    没有 `_red_lines`；`Path.stem` 又已经剥掉 `.json`，于是「拿 `stem` 去 `removesuffix(
    ARTIFACT_SUFFIX)`」**永远删不掉**（`"ping_red_lines".endswith("_red_lines.json")` 为假）——
    两个集合恒不相等，这条锁**恒红**，而恒红的锁与没有锁是一回事（§四）。实测：修这句之前
    `4 failed`，修完之后只剩三条覆盖闭合的红 —— 那是**真缺口**（169 条判别器撞到 63 条），
    处置计划记在 `AGENTS.md` 缺陷 41 的收口段里。
    """
    drivers = {p.name.removesuffix(_MUT_SUFFIX) for p in PERF.glob("*_mutations.py")}
    recorded = {p.name.removesuffix(lock_coverage.ARTIFACT_SUFFIX) for p in _artifacts()}
    assert drivers == recorded, (
        "变异驱动与「红在哪一行」的产物对不上：\n"
        f"  有驱动却没产物（矩阵跑了但没有任何东西记下红源，缺口不会响）："
        f"{sorted(drivers - recorded)}\n"
        f"  有产物却没驱动（残留，或驱动被改名）：{sorted(recorded - drivers)}")


# ── 覆盖闭合：每条判别器都有变异撞过，且没有空转变异 ───────────────────────────

@pytest.mark.parametrize("artifact", _artifacts(), ids=lambda p: p.stem)
def test_discriminators_and_red_lines_are_the_same_set(artifact: pathlib.Path):
    data = json.loads(artifact.read_text(encoding="utf-8"))
    domain, hits = data["domain"], data["mutations"]
    uncovered, vacuous = lock_coverage.gaps(domain, hits, ROOT)

    assert uncovered == [], (
        f"{data['driver']} 覆盖不到这些判别器（判别器还在、但没有任何变异撞它 —— "
        "「变异红 ≠ 判别器起作用」的第四种形态，不会有人发现）：\n" +
        "\n".join(f"  {lock_coverage.discriminators(ROOT / loc.rsplit(':', 1)[0])[int(loc.rsplit(':', 1)[1])]}"
                  f"    [{loc}]" for loc in uncovered) +
        "\n处置只有两种：补一条撞它的变异，或**删掉这条判别器**"
        "（在任何合法变异下都红不了 = 死判据）。本锁不设豁免名单。")

    assert vacuous == [], (
        f"{data['driver']} 里这些变异一条判别器都没撞到（空转变异 —— 它红了，"
        f"但红的不是任何一条判据，红源说不清）：{vacuous}")


# ── 元锁自身的正控与负控：合成输入，不碰仓内 ──────────────────────────────────

_SYNTH = '''\
def fail(msg):
    raise AssertionError(msg)


def hand_off(msg):
    fail(msg)


def check(x):
    if x < 0:
        raise ValueError("neg")
    if x > 10:
        hand_off("boom")
    assert x < 100, "too big"
'''


def test_pure_raiser_bodies_are_not_discriminators(tmp_path):
    """包装内部的 `raise` 与其转手调用**不算**判别器，只算调用点。

    这条不是风格：算上它，所有经由包装的分支会在 `--tb=long` 里塌成同一条最深帧。
    """
    p = tmp_path / "synth_lock.py"
    p.write_text(_SYNTH, encoding="utf-8")
    assert sorted(lock_coverage.discriminators(p)) == [11, 13, 14]


def test_uncovered_discriminator_is_named(tmp_path):
    """负控 1：判别器没被撞到 → 点名它。"""
    p = tmp_path / "synth_lock.py"
    p.write_text(_SYNTH, encoding="utf-8")
    uncovered, vacuous = lock_coverage.gaps(
        ["synth_lock.py"], {"M-1": ["synth_lock.py:11"]}, tmp_path)
    assert uncovered == ["synth_lock.py:13", "synth_lock.py:14"]
    assert vacuous == []


def test_mutation_red_outside_any_discriminator_is_named(tmp_path):
    """负控 2：红了但**一条判别器都没撞到** → 点名那条变异。"""
    p = tmp_path / "synth_lock.py"
    p.write_text(_SYNTH, encoding="utf-8")
    uncovered, vacuous = lock_coverage.gaps(
        ["synth_lock.py"], {"M-2": ["synth_lock.py:9"]}, tmp_path)
    assert vacuous == ["M-2"]
    assert uncovered == ["synth_lock.py:11", "synth_lock.py:13", "synth_lock.py:14"]


_SYNTH_RAISES = '''\
import pytest


def check(x):
    if x < 0:
        with pytest.raises(ValueError):
            boom(x)
    with pytest.raises(
        TypeError
    ):
        boom(x)
    pytest.raises(KeyError, boom, x)
    assert x < 100
'''


def test_raises_are_discriminators_at_the_with_line(tmp_path):
    """`pytest.raises` 算判别器，行号取 `with` 那一行 —— 裸调用形式取调用行。

    三条判据都不是风格问题：
      - 不收它 →「该抛而没抛」这一类分支在 D 里根本不存在，缺的判别器看不见；
      - `with` 形式取 `ast.With` 的行号（实测 pytest 红在 `with` 那行，多行写法也是）；
      - 多行写法里 `context_expr` 的调用在下一行（上面第 9 行的 `TypeError`），若也计数
        就凭空多出一条永远没人撞的判别器 —— 那正是本锁要防的「空转」的另一副面孔。
    """
    p = tmp_path / "synth_raises.py"
    p.write_text(_SYNTH_RAISES, encoding="utf-8")
    assert sorted(lock_coverage.discriminators(p)) == [6, 8, 12, 13]


def test_intermediate_frames_are_not_discriminators(tmp_path):
    """中间帧（测试函数 → 辅助函数的调用点）不是判别器，不该把变异撑成「非空转」。"""
    p = tmp_path / "synth_lock.py"
    p.write_text(_SYNTH, encoding="utf-8")
    _, vacuous = lock_coverage.gaps(
        ["synth_lock.py"], {"M-3": ["synth_lock.py:9"]}, tmp_path)
    assert vacuous == ["M-3"]
    uncovered, vacuous = lock_coverage.gaps(
        ["synth_lock.py"], {"M-3": ["synth_lock.py:9", "synth_lock.py:11"]}, tmp_path)
    assert vacuous == []
    assert uncovered == ["synth_lock.py:13", "synth_lock.py:14"]


def test_red_lines_are_mapped_back_to_the_pristine_coordinates():
    """变异插了一行 → 运行时的行号整体 +1，必须搬回变异前的坐标系。

    不搬就是 G-17/G-19 那个假象：真被撞到的判别器记成「没人撞」，而那条变异记成「空转」。
    """
    shifted = "# 头部插一行\n" + _SYNTH
    lines, problems = lock_coverage.realign_hits(
        {"synth_lock.py": _SYNTH}, {"synth_lock.py": shifted},
        {"synth_lock.py:12", "synth_lock.py:14", "synth_lock.py:15", "synth_lock.py:99"})
    assert problems == []
    assert lines == {"synth_lock.py:11", "synth_lock.py:13", "synth_lock.py:14"}


def test_untouched_files_keep_their_coordinates():
    """没被变异动过的文件不参与搬移 —— 硬搬等于给每一个正常红源凭空加偏移。"""
    lines, problems = lock_coverage.realign_hits(
        {"synth_lock.py": _SYNTH}, {}, {"synth_lock.py:11", "other_lock.py:5"})
    assert problems == []
    assert lines == {"synth_lock.py:11", "other_lock.py:5"}


def test_a_mutation_that_changes_the_discriminator_sequence_is_refused():
    """负控：变异增删了靶子自己的判别器 → 按次序对齐已经是错的，必须报出来（调用方拒跑）。

    这时**一条红源都不搬**：搬出来的东西没法核对，比没有更坏。
    """
    mutated = _SYNTH.replace('    assert x < 100, "too big"',
                             '    assert x < 100, "too big"\n    assert x != 7')
    lines, problems = lock_coverage.realign_hits(
        {"synth_lock.py": _SYNTH}, {"synth_lock.py": mutated}, {"synth_lock.py:12"})
    assert len(problems) == 1 and "判别器序列" in problems[0]
    assert lines == set()


def test_full_coverage_is_silent(tmp_path):
    """正控：全覆盖时两个缺口都空 —— 否则上面的判据可能恒真。"""
    p = tmp_path / "synth_lock.py"
    p.write_text(_SYNTH, encoding="utf-8")
    assert lock_coverage.gaps(
        ["synth_lock.py"],
        {"M-1": ["synth_lock.py:11"], "M-2": ["synth_lock.py:13", "synth_lock.py:14"]},
        tmp_path) == ([], [])


# ── 判档：跑不起来 / skip 都不得与绿共用一个信号 ──────────────────────────────

def test_a_runaway_is_not_a_pass():
    """拿不到汇总行 → `RUNAWAY`，不是 `green`。

    这是三个驱动共用的那一处：原先 `_run` 回 `"?"`，驱动判 `"failed" in summary` 落空 →
    记 green。于是「子进程崩了」与「全部通过」共用一个信号，实测本机 Windows 上
    `test_text_failure_messages.py` import onnxruntime 即崩，那个靶子下**每条**变异都「绿」。
    """
    assert lock_coverage.outcome(lock_coverage.RUNAWAY) == lock_coverage.RUNAWAY
    # 判档只看字面，认不出「没有汇总行」—— 把「拿不到」变成 RUNAWAY 是 `_run` 的责任，
    # 这一条只保证「已经拿到 RUNAWAY 之后不会被读成绿」。
    for s in ("1 failed, 3 passed in 0.4s", "2 errors in 1.1s", "1 error in 0.2s"):
        assert lock_coverage.outcome(s) == lock_coverage.RED, s


def test_a_skipped_run_is_not_a_pass():
    """汇总行含 skipped → `SKIP`，不是 `green`（判据在空转，绿的是空集）。"""
    for s in ("4 passed, 2 skipped in 3.3s", "2 skipped in 0.1s"):
        assert lock_coverage.outcome(s) == lock_coverage.SKIP, s
    assert lock_coverage.outcome("4 passed in 3.3s") == lock_coverage.GREEN


def test_baseline_accepts_skip_but_not_red_or_runaway():
    """基线上 skip 可用、红与跑不起来不可用 —— 两侧都要控。

    只控一侧会让判据恒真/恒假：`test_storage_ping.py` 基线本身就是「4 passed, 2 skipped」
    （B-1/B-1b 的锁版 sqlite 前提），若把 skip 判成不可用，那个驱动**永远**开不了跑。
    """
    assert lock_coverage.baseline_ok("19 passed in 6.6s")
    assert lock_coverage.baseline_ok("4 passed, 2 skipped in 3.3s")
    assert not lock_coverage.baseline_ok("1 failed in 0.4s")
    assert not lock_coverage.baseline_ok(lock_coverage.RUNAWAY)
