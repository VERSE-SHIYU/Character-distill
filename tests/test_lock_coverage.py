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
人为规定。本文件自己的判据由下面那批**合成输入**用例承担（元锁写不了「撞自己的变异」：
给它写驱动就会把本文件的全部 assert 拉进覆盖域，一夜之间多出几十条缺口 —— 那不是守卫，
是洪水）。名单与「只许减少」的来由见 `lock_coverage` 的模块 docstring。
"""
from __future__ import annotations

import json
import pathlib

import pytest

import lock_coverage
import lock_coverage_gaps
import policy_table

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
    """判别器集合与「变异实际红到的行」集合**相等** —— 除开名单上登记的已知缺口。

    命题在名单式落地时从「全覆盖」换成「**只许减少**」：84 条今天是撞不到的，锁天天红，
    而一直红的锁等于没有锁。换成两个方向后，它今天就能成立，且原先那条规矩（新长出来的
    判别器不许被豁免）由方向 ① 原样继承。
    """
    data = json.loads(artifact.read_text(encoding="utf-8"))
    domain, hits = data["domain"], data["mutations"]
    controls = set(data.get("controls", ()))
    # 三类必须互斥：一条变异只能属于「该红」「该绿」「本环境跳过」之一。重叠就是驱动把
    # 它同时记进了两处 —— 那会让同一个信号被读成两种东西（「反证」↔「空转」的同一个病）。
    assert not (controls & set(hits)) and not (controls & set(data["skipped"])), (
        f"{data['driver']}：mutations / controls / skipped 三类不互斥")
    uncovered, vacuous = lock_coverage.gaps(domain, hits, ROOT)

    assert vacuous == [], (
        f"{data['driver']} 里这些变异一条判别器都没撞到（空转变异 —— 它红了，"
        f"但红的不是任何一条判据，红源说不清）：{vacuous}")

    allow = lock_coverage_gaps.ALLOWED_GAPS.get(data["driver"], {})
    # 映射的**键**才是行号（用于把红源指回现场），判别器的身份是那个元组（里面没有行号）。
    identity = lock_coverage.gap_keys(uncovered, ROOT)     # 文件:行号 → 稳定身份
    actual = set(identity.values())

    # ① 现场未覆盖 ⊄ 名单 —— 长出了一条没人登记过的缺口。这一条是「不设豁免名单」那条
    # 老规矩的继承：**新长出来的判别器不许被豁免**，名单是账，不是许可证。
    new = policy_table.unexpected(actual, allow)
    assert not new, (
        f"{data['driver']} 长出了名单上没有的缺口（判别器还在、但没有任何变异撞它 —— "
        "「变异红 ≠ 判别器起作用」的第四种形态，不会有人发现）：\n" +
        "\n".join(f"  {loc}  {identity[loc][1]}"
                  f"    [同文本第 {identity[loc][2] + 1} 处]" for loc in sorted(uncovered)
                  if identity[loc] in new) +
        "\n处置只有两种：补一条撞它的变异，或**删掉这条判别器**"
        "（在任何合法变异下都红不了 = 死判据）。要留着，就往 "
        "tests/lock_coverage_gaps.py 登记，并写清「要撞到它得构造什么」。")

    # ② 名单 ⊄ 现场未覆盖 —— 名单里的条目已经不是缺口了。**这一条才是关键**：它逼着
    # 「补上就同步删」，否则名单会退化成只增不减的手工清单（本轮踩过两次的形态）。
    stale = policy_table.stale_keys(allow, actual)
    assert not stale, (
        f"{data['driver']} 的名单里有**已不再是缺口**的条目"
        "（判别器被撞到了 / 被删了 / 文本改了 —— 后两种会让这里与方向 ① 同时红）：\n" +
        "\n".join(f"  {rel}  {snip!r}    [同文本第 {nth + 1} 处]"
                  for rel, snip, nth in sorted(stale)) +
        "\n从 tests/lock_coverage_gaps.py 删掉这几行 —— 名单只许减少。")

    assert not policy_table.empty_reasons(allow), (
        f"{data['driver']} 的名单里有空理由条目：{sorted(policy_table.empty_reasons(allow))}")
    assert not lock_coverage_gaps.placeholder_reasons(allow), (
        f"{data['driver']} 的名单里有占位语理由（又短、又用的是占位词 —— 写清「要撞到它得"
        f"构造什么」，否则清名单的人不知道该补哪条变异）："
        f"{sorted(lock_coverage_gaps.placeholder_reasons(allow))}")


def test_the_gap_list_names_only_real_drivers():
    """名单的**驱动段**必须都是真驱动 —— 驱动改名/删除后留下的孤儿段，否则没人看得见。

    与方向 ② 同一个病，只是在上一层：方向 ② 防「判别器补上了却留着名单条目」，这一条防
    「驱动没了却留着整段名单」。孤儿段不会被参数化跑到（那个驱动不在了，`_artifacts()`
    里没有它的产物行），所以它既不红也不绿 —— 正是「只增不减」要防的那副面孔。

    比的是产物里的 `driver` 字段（**路径形态**，如 `tests/perf/ping_mutations.py`），
    因为名单就是按它分段的 —— 拿 `Path.stem` 去比会两边永远不等、这条判据恒红（上面
    「驱动 ↔ 产物」那条锁踩过同一个坑）。
    """
    drivers = {json.loads(p.read_text(encoding="utf-8"))["driver"] for p in _artifacts()}
    ghosts = lock_coverage_gaps.unknown_drivers(lock_coverage_gaps.ALLOWED_GAPS, drivers)
    assert not ghosts, (
        f"tests/lock_coverage_gaps.py 里这些驱动段没有对应的驱动（改名后的残留？）："
        f"{sorted(ghosts)} —— 删掉整段，或把驱动名改回来")


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


_SYNTH_TRANSPLANT = '''\
class Pinger:
    async def ping(self):
        if self._exc is not None:
            raise self._exc


def reraiser():
    try:
        pass
    except Exception:
        raise


def check(x):
    if x < 0:
        raise ValueError("neg")
    if x < 1:
        raise NegError


class NegError(Exception):
    pass
'''


def test_only_locally_constructed_raises_are_discriminators(tmp_path):
    """负控：`raise self._exc`（转抛存起来的异常）与裸 `raise` **不算**判别器。

    两者都不是「判断被守对象」的分支，而是让测试替身/钩子得以工作的**动作** —— 任何合法
    变异都撞不到，收进来就是**假缺口**（实测：`test_health_probe_targets.py:133`、
    `test_health_ready.py:48` 的 `raise self._exc`，两条都在 `if self._exc is not None:`
    之下，不是纯抛错包装的体，所以它们原先各占 D 的一席）。
    这里原本还有第三条实测数据：`test_text_failure_messages.py` 里 import 钩子那条
    `raise _ParserRuntimeTouched(fullname)` —— **就地构造**，收窄后留在 D，不是假缺口，
    只是本机不可达（环境性，另账）。**C 落地时整条钩子被删了**：解析器运行时已不在仓里，
    「没触达」成了对空集的断言（假绿），钩子换成静态守卫，那条 `raise` 随之消失。
    """
    p = tmp_path / "synth_transplant.py"
    p.write_text(_SYNTH_TRANSPLANT, encoding="utf-8")
    assert sorted(lock_coverage.discriminators(p)) == [16, 18]


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


_SYNTH_GAPS = '''\
def check(x):
    if x < 0:
        raise ValueError("neg")
    assert x > 0
    assert x > 0
'''


def test_gap_keys_survive_a_line_shift(tmp_path):
    """名单的键是判别器的**文本**，不是行号 —— 头部插一行，键必须一模一样。

    **这不是假想的顾虑，是落地前实测踩到的。** `test_text_failure_messages.py` 只改了一段
    docstring（`224fd68`），L6 入口面那条静态守卫的 assert 就从 473 滑到 482。按行号做键
    的话，T-3 的红源当场落到一个不再是判别器的行号上：那条变异被记成**空转**，同时 482
    凭空多出一条「新缺口」。文本做键，同一份名单在内容相同的树上只有一个答案。
    """
    p = tmp_path / "synth_gaps.py"
    p.write_text(_SYNTH_GAPS, encoding="utf-8")
    before = lock_coverage.gap_keys(
        [f"synth_gaps.py:{n}" for n in sorted(lock_coverage.discriminators(p))], tmp_path)

    p.write_text("# 头部插一行\n" + _SYNTH_GAPS, encoding="utf-8")
    after = lock_coverage.gap_keys(
        [f"synth_gaps.py:{n}" for n in sorted(lock_coverage.discriminators(p))], tmp_path)
    assert set(before.values()) == set(after.values())


def test_gap_keys_disambiguate_identical_snippets_by_line_order(tmp_path):
    """同一文件里出现**同文本**判别器时，用行号次序区分 —— 这份合成文件里有两处 `assert x > 0`。

    实仓里就有：`assert r.status_code == 400, r.text` 在一份文件里出现 4 次、
    `assert await store.ping() is None` 出现 2 次。只按文本做键会让后一处无处安放。
    """
    p = tmp_path / "synth_gaps.py"
    p.write_text(_SYNTH_GAPS, encoding="utf-8")
    keys = lock_coverage.gap_keys(["synth_gaps.py:4", "synth_gaps.py:5"], tmp_path)
    assert keys == {
        "synth_gaps.py:4": ("synth_gaps.py", "assert x > 0", 0),
        "synth_gaps.py:5": ("synth_gaps.py", "assert x > 0", 1),
    }


def test_both_directions_of_the_gap_list_are_controlled(tmp_path):
    """双向各一负控 + 全对上的正控 —— 三个方向都要控，只控一侧会让「恒空」永远绿。

    ① 现场有、名单没有 → 点名那条**新缺口**（名单是账，不是许可证）；
    ② 名单有、现场没有 → 点名那条**陈旧条目**（逼着「补上就同步删」）。
    """
    p = tmp_path / "synth_gaps.py"
    p.write_text(_SYNTH_GAPS, encoding="utf-8")
    identity = lock_coverage.gap_keys(["synth_gaps.py:4", "synth_gaps.py:5"], tmp_path)
    actual = set(identity.values())
    first, second = identity["synth_gaps.py:4"], identity["synth_gaps.py:5"]

    assert policy_table.unexpected(actual, {first: "理由"}) == {second}
    assert policy_table.stale_keys({first: "理由", second: "理由"}, {first}) == {second}

    both = {first: "理由一", second: "理由二"}
    assert policy_table.unexpected(actual, both) == set()
    assert policy_table.stale_keys(both, actual) == set()


def test_placeholder_reasons_and_unknown_drivers_are_named():
    """名单自身的两条机械判据各一正一负：占位语理由、孤儿驱动段。

    `placeholder_reasons` 是**③层字符串代理**（0 层要问的「清名单的人能否据此知道该补
    什么」机器判不了），可接受之处在失效方向是**红不是绿**：理由里写了这些词就当场红，
    逼人看一眼。它**不是**这条要求的全部守卫 —— 写得含糊但不含这些词的（如「不好写」）
    照样混得过去。孤儿驱动段则是方向 ② 在上一层的同一个病：驱动没了，整段名单也没人看。

    第三条正控是**实测踩出来的**：代理光看「出现占位词」会红在**正确的理由**上 ——
    真名单里有一条理由写「文案里的占位符要真的被填」，「占位符」是 Python 格式化占位符
    这个领域词，却被判成占位语。这种红比漏判更坏（逼人改写正确句子去迎合代理），
    所以加了长度下限（真理由最长的一批，最短也有 25 字），这条正控把它钉住。
    """
    good = {("f.py", "assert x > 0", 0): "要撞它得让 f.py 的 x 恒为正 —— 改那条判断即可"}
    assert lock_coverage_gaps.placeholder_reasons(good) == set()
    assert lock_coverage_gaps.placeholder_reasons(
        {("f.py", "assert x > 0", 0): "待补"}) == {("f.py", "assert x > 0", 0)}
    # 回归：长理由里出现领域词「占位符」，曾把一条正确理由判红
    assert lock_coverage_gaps.placeholder_reasons(
        {("f.py", "assert x > 0", 0):
         "文案里的占位符要真的被填上，不是原样上屏 —— 要撞它得跳过 .format()"}) == set()
    # 短到没有信息量的占位语，换哪个词都红
    for junk in ("以后再补", "暂时待定", "TBD", "占位"):
        assert lock_coverage_gaps.placeholder_reasons(
            {("f.py", "assert x > 0", 0): junk}) == {("f.py", "assert x > 0", 0)}

    real = {"d_mutations.py", "e_mutations.py"}
    assert lock_coverage_gaps.unknown_drivers({"d_mutations.py": {}}, real) == set()
    assert lock_coverage_gaps.unknown_drivers({"gone_mutations.py": {}}, real) == {
        "gone_mutations.py"}


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


def test_the_two_causes_of_an_unusable_baseline_are_distinct():
    """「跑不起来」与「跑起来了但红」必须判成**两种**成因（缺陷 45）。

    两者的下一步动作不同：前者修环境（容器腿缺 pytest/onnxruntime）、后者修锁。共用一个
    信号时，一个人拿着「基线不绿」去翻锁，而真实原因是 pytest 压根没装 —— 拒跑了，但指错方向。
    """
    for usable in ("19 passed in 6.6s", "4 passed, 2 skipped in 3.3s"):
        assert lock_coverage.baseline_verdict(usable) == "", usable
    for red in ("1 failed in 0.4s", "1 failed, 3 passed in 0.4s", "2 errors in 1.1s"):
        assert lock_coverage.baseline_verdict(red) == lock_coverage.BASELINE_RED, red
    assert lock_coverage.baseline_verdict(lock_coverage.RUNAWAY) == lock_coverage.BASELINE_UNUSABLE
    assert lock_coverage.BASELINE_RED != lock_coverage.BASELINE_UNUSABLE


def test_refuse_on_baseline_exits_by_cause(capsys):
    """拒跑的退出码按成因分档，且文案点名靶子（三种组合都要控）。

    脚本侧原先只有 code 2 一个信号：CI 日志里看得出「拒跑」，看不出该修环境还是修锁。
    """
    t1, t2 = "tests/test_a.py", "tests/test_b.py"

    code = lock_coverage.refuse_on_baseline({t1: lock_coverage.BASELINE_RED})
    out = capsys.readouterr().out
    assert code == lock_coverage.EXIT_BASELINE_RED
    assert t1 in out and "先修基线" in out

    code = lock_coverage.refuse_on_baseline({t1: lock_coverage.BASELINE_UNUSABLE})
    out = capsys.readouterr().out
    assert code == lock_coverage.EXIT_BASELINE_UNUSABLE
    assert t1 in out and "先修环境" in out and "先修基线" not in out

    # 两种同时出现：按「不可用」退 —— 环境没修好之前，另一支的结论本来也拿不到。
    code = lock_coverage.refuse_on_baseline({
        t1: lock_coverage.BASELINE_RED, t2: lock_coverage.BASELINE_UNUSABLE})
    out = capsys.readouterr().out
    assert code == lock_coverage.EXIT_BASELINE_UNUSABLE
    assert t1 in out and t2 in out
