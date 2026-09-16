# -*- coding: utf-8 -*-
"""通用机械校验层：**一个锁文件里「会导致失败的分支」↔「变异实际红在哪一行」的覆盖闭合**。

**与领域无关。** 本模块不认识 compose、不认识 pg、不认识路由器，也不认识任何具体服务名 ——
输入永远是一份路径。使用方是本仓的策略锁与它们的变异驱动，`tests/test_lock_coverage.py`
是它自己的锁。

**判别器的可判定定义**（四类 AST 节点，每个一条，行号取节点自身 `lineno`）：
  (a) `ast.Assert` —— 每条一次。`assert x, msg` 算一条（msg 只是文案）。
  (b) `ast.Raise` —— **仅当它不位于「纯抛错包装」体内**、**且是就地构造或点名异常的**
      （`raise X(...)` / `raise X`）时算一条。`raise self._exc`（转抛**存起来的**异常）与裸
      `raise`（转抛当前异常）**不算** —— 见下面「(b) 为什么不收转抛」。
  (c) 对「纯抛错包装」的调用 —— **每个调用点**算一条，行号 = 调用所在行。
  (d) `pytest.raises(...)` —— **`with ...:` 形式取 `ast.With` 的行号**，裸调用形式取该调用
      的行号。预期没抛时它自己红（`Failed: DID NOT RAISE`）。

**(b) 为什么不收转抛 —— 定义过宽会造出「假缺口」。** 实测三处：`tests/test_health_probe_
targets.py:133` 与 `tests/test_health_ready.py:48` 的 `raise self._exc`（**测试替身的失败
注入**：`_exc` 为 None 时静默成功，是替身的机制）与 `tests/test_text_failure_messages.py:477`
的 import 钩子里的 `raise ...`（钩子的**动作**）。三处都不是「判断被守对象」的分支，任何
合法变异都撞不到它们 —— 按旧定义（每一条 `ast.Raise`）它们进 D 而不进 E，于是元锁把**机制**
记成**缺口**。**删代码不是处置**（删了替身与钩子就不工作），收窄定义才是：**「就地构造或
点名一个异常」与「把一个外来异常转抛出去」是两种东西**，只有前者是判断。这与本轮另立的
那条互为镜像：**定义不全 → 假空转 + 假覆盖；定义过宽 → 假缺口。**

**(d) 是实测补的，不是想当然。** 它原先不在定义里，于是「`pytest.raises` 该抛而没抛」这一类
分支在两边都不存在 —— 收了它才会发现某些判别器**从没被任何变异撞过**（缺的是判别器，不是
变异）。行号取 `ast.With` 而非其中的调用，也是实测定的：多行写法

    with pytest.raises(          # ← pytest 红在这一行
        ComposeFactError
    ):

里调用在下一行，按调用取会与 `with` 差一行，凭空多出一条不存在的判别器；故 `context_expr`
那个调用**不再单独计数**（键仍是行号，同行的裸调用形式不受影响）。

**「纯抛错包装」= 递归定义的不动点**：函数体（剥掉 docstring 与 `pass` 后）恰好一条语句，
且该语句是 `raise X(...)`，或是对另一个纯抛错包装的调用。

**这个划法不是风格选择，是被运行侧倒逼出来的唯一解。** pytest 的 `--tb=line` 给的是
**最深帧** —— 包装函数内部那条 `raise`。若把那条 `raise` 也算成判别器，则所有经由包装的
分支会**塌成同一条**（实测：`_closed_keys` 里的 `raise` 让 G-6 与 G-7 两条变异都打印
`test_pg_gate.py:234`）；若反过来只数 `raise`、不算调用点，则这些分支**一条都不存在**
（没有任何一条 `raise` 写在自己的分支上）。(b)/(c) 互补，为的是与 `--tb=long` 的帧对齐。

**两套坐标系，必须先对齐。** 判别器集合从**变异前**的文件算出来，红行号却来自**变异后**那次
运行 —— 只要变异往靶子文件里插了一行，插点之后所有判别器的行号就整体平移。直接把两套行号
求交，会同时造出两种假象：真被撞到的判别器记成「没人撞」，而那条变异记成「空转」（它红在
一个不是判别器的行号上）。实测 G-17/G-19 就是这样各差 1 行 —— 豁免表在 183 行，被撞的断言在
564 行，表里插一行，断言就跑到 565 去。对齐用 `realign_hits()`：按判别器在文件里的**出现次序**
对齐（插/删行不改变次序，只改变行号）；次序对不上就报出来，那时两套坐标不可比、红源说不清。

**从「不设名单」改到「已知缺口名单（只许减少）」—— 命题变了，理由要如实记。** 本模块原先
写的是「不设豁免名单」：任何合法变异下都红不了的判别器 = 死判据，处置只有删掉它。那条规矩
**没有错，只是不完整** —— 它没有回答「今天就已经有一批撞不到的判别器时怎么办」。实测的
答案是：全矩阵跑完仍有 84 条（26 / 19 / 39），而**一直红的锁等于没有锁** —— 生产上 PDF 已坏
73 天，锁天天红，没有任何人看得出来。于是命题从「全覆盖」换成「**只许减少**」，名单在
`tests/lock_coverage_gaps.py`，双向对账由 `tests/test_lock_coverage.py` 用 `policy_table`
的两个差集函数做（见 AGENTS.md 缺陷 41 的收口段）：

  - ① 现场未覆盖 ⊆ 名单 —— 新增缺口不在名单里就红；
  - ② 名单 ⊆ 现场未覆盖 —— 补上了却没删名单也红。

原先那条规矩要防的东西**没有丢**：它现在由方向 ① 承担（新长出来的判别器不许被豁免），
而方向 ② 额外堵住「名单只增不减」。名单每条写清**要撞到它得构造什么**，否则清名单的人
不知道该补哪条变异。

**递归的终止是判据自身推出的，不是人为规定的。** 覆盖域取自**变异驱动自己的事实字段**
（驱动表里每条变异的靶子文件，由 `domain_of()` 从驱动数据推出，不另立清单）。今天没有任何
驱动把 `test_lock_coverage.py` 列为靶子，所以元锁**天生不在域内** —— 这不是豁免，是跟着
驱动事实走的；将来谁真给元锁写了驱动，它自动进域，元锁就得给自己配变异。没有「永远免除」
的口子。
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from collections.abc import Iterable, Mapping, Sequence

ARTIFACT_SUFFIX = "_red_lines.json"

# `--tb=long` 的两处「位置」写法，**两种都要收**：
#   帧头     `路径:行号:` 后只有空白 —— `pytest` 只给**中间**帧，最深帧没有帧头；
#   结尾行   `路径:行号: AssertionError` —— 那是**最深帧**的位置，正是「句内 assert」与
#            「包装内部的 raise」唯一的出处。
# 只收帧头会漏掉后者（实测：24 条变异里 9 条记成空，因为它们红在句内 assert 上）。
# 收全之后由 `gaps()` 与判别器集合求交 —— 包装内部那条 `raise`（如 `fail` 里的 281）不在
# 判别器集合里，交集自动把它滤掉。
_FRAME = re.compile(r"^(?P<path>.+?):(?P<line>\d+):\s*$")
_TAIL = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<exc>[A-Za-z_][\w.]*)$")


def _strip_decorative(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    """剥掉 docstring 与 `pass`，剩下的是「这篇函数的实际内容」。"""
    out = []
    for i, s in enumerate(body):
        if i == 0 and isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) \
                and isinstance(s.value.value, str):
            continue
        if isinstance(s, ast.Pass):
            continue
        out.append(s)
    return out


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    return None


def _pure_raisers(tree: ast.AST) -> set[str]:
    """递归定义的不动点：体恰好一条语句 = `raise`，或 = 对另一个纯抛错包装的调用。"""
    shape: dict[str, object] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = _strip_decorative(fn.body)
        if len(body) != 1:
            continue
        stmt = body[0]
        if isinstance(stmt, ast.Raise):
            # 体是 `raise X(...)` / `raise X` 才算包装 —— 与判别器那条**同一把尺子**：
            # 体是裸 `raise` / `raise self._exc` 的函数是转手，不是包装（今天是空集，但
            # 定义不一致会让下一个读 docstring 的人按另一把尺子判）。
            if _is_constructed(stmt):
                shape[fn.name] = True                 # True = 直接 raise
        elif isinstance(stmt, ast.Expr) and _callee_name(stmt.value):
            shape[fn.name] = _callee_name(stmt.value)  # 名字 = 转手调用的那个包装

    pure: set[str] = set()
    changed = True
    while changed:                                    # 不动点：链尾先成立，再往回传
        changed = False
        for name, target in shape.items():
            if name in pure:
                continue
            if target is True or target in pure:
                pure.add(name)
                changed = True
    return pure


def _is_raises_call(node: ast.AST) -> bool:
    """`pytest.raises(...)`（也认 `from pytest import raises` 后的裸名）。"""
    return isinstance(node, ast.Call) and _callee_name(node) == "raises"


def _is_constructed(node: ast.Raise) -> bool:
    """`raise X(...)` / `raise X` —— 就地构造或点名一个异常；那是「判断被守对象」。

    `raise self._exc`（转抛**存起来的**异常，如测试替身的失败注入）与裸 `raise`（转抛当前
    异常）不是判断，是本模块 docstring (b) 里说的「机制」。收进来会造出假缺口：没有任何
    合法变异能撞到它们 —— 它们不是被守对象的分支，是让替身/钩子得以工作的动作。
    """
    exc = node.exc
    if exc is None:
        return False
    return isinstance(exc, (ast.Call, ast.Name))


def _discriminators_from_src(src: str) -> dict[int, str]:
    tree = ast.parse(src)
    pure = _pure_raisers(tree)

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def inside_pure_raiser(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and cur.name in pure:
                return True
            cur = parents.get(cur)
        return False

    # `with pytest.raises(...)` 里那个 context_expr 调用不单独计数 —— 判别器是那条 `with`
    # 语句（pytest 红的也是它那一行）。多行写法里二者行号不同，两边都收会凭空多一条。
    context_exprs = {id(it.context_expr) for n in ast.walk(tree)
                     if isinstance(n, ast.With) for it in n.items}

    out: dict[int, str] = {}
    for node in ast.walk(tree):
        is_raises_with = (isinstance(node, ast.With)
                          and any(_is_raises_call(it.context_expr) for it in node.items))
        is_raises_call = _is_raises_call(node) and id(node) not in context_exprs
        is_disc = (
            isinstance(node, ast.Assert)
            or (isinstance(node, ast.Raise) and _is_constructed(node))
            or (isinstance(node, ast.Call) and _callee_name(node) in pure)
            or is_raises_with
            or is_raises_call
        )
        if not is_disc or inside_pure_raiser(node):
            continue
        if is_raises_with:
            seg = _line_of(src, node)
        else:
            seg = ast.get_source_segment(src, node) or ""
        out[node.lineno] = " ".join(seg.split())[:110]
    return out


def _line_of(src: str, node: ast.AST) -> str:
    """节点起始那一行的源码。用于 `with` 这类**整块**跨多行的语句。"""
    lines = src.splitlines()
    return lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""


def discriminators(path: str | pathlib.Path) -> dict[int, str]:
    """锁文件里所有「会导致失败的分支」：行号 → 该分支源码的一行摘要。"""
    return _discriminators_from_src(pathlib.Path(path).read_text(encoding="utf-8"))


def realign_hits(pristine: Mapping[str, str], mutated: Mapping[str, str],
                 hits: Iterable[str]) -> tuple[set[str], list[str]]:
    """把红行号从「变异后坐标系」搬回「变异前坐标系」。

    `pristine` / `mutated` 以仓内相对 posix 路径为键，值是那一次的源文本；只有两边都给得出
    的 .py 才参与搬移（没被变异动过的文件坐标系本来相同，原样透传）。

    返 `(搬移后的位置, 两套坐标不可比的文件说明)`。**不可比不是错误、是拒绝条件** ——
    变异增删了那个文件自己的判别器时，按次序对齐就已经是错的，必须让调用方拒跑而不是硬搬。
    但只在**真有红源落进那个文件**时才算数：改动别处的文件、而红源一条都不指向它，两套坐标
    一样用（实测 G-22 改 `compose_model.py` 就删掉了它自己的一条判别器，而红源全在靶子上）。

    行号落在「变异后没有任何判别器」的位置上就丢弃：包装内部那条 `raise`、中间帧都在这一类，
    `gaps()` 本来也会把它们与判别器集合求交滤掉，这里少走一步而已。
    """
    pairs: dict[str, tuple[list[int], list[int]]] = {}
    incomparable: dict[str, str] = {}
    for rel, mut_src in mutated.items():
        pri_src = pristine.get(rel)
        if pri_src is None:
            continue
        before, after = _discriminators_from_src(pri_src), _discriminators_from_src(mut_src)
        b, a = sorted(before.items()), sorted(after.items())
        if [s for _, s in b] != [s for _, s in a]:
            incomparable[rel] = (
                f"{rel}：变异改变了它的判别器序列（{len(b)} 条 → {len(a)} 条），"
                "红行号搬不回变异前的坐标系")
            continue
        pairs[rel] = ([n for n, _ in b], [n for n, _ in a])

    out: set[str] = set()
    problems: list[str] = []
    for loc in hits:
        rel, _, n = loc.rpartition(":")
        if rel not in mutated:            # 没被变异动过 → 两套坐标系本来相同
            out.add(loc)
            continue
        why = incomparable.get(rel)
        if why is not None:               # 动过却对不上 → 无坐标可搬，且要让人拒跑
            if why not in problems:
                problems.append(why)
            continue
        before, after = pairs[rel]
        if n.isdigit() and int(n) in after:
            out.add(f"{rel}:{before[after.index(int(n))]}")
    return out, problems


def red_lines(output: str, root: str | pathlib.Path) -> set[str]:
    """从 pytest `--tb=long` 的输出里取所有「位置」，归一成仓内相对 posix 的 `文件:行号`。

    退到 `文件:行号` 不需要更细的粒度：判别器的定义粒度就是「一行」，两边同一把尺子即可。
    返回值是**原始帧位置**（含中间帧），与判别器集合求交由 `gaps()` 一次做完 ——
    交集放在一处，免得调用方各自记得滤。
    """
    base = pathlib.Path(root).resolve()
    out: set[str] = set()
    for ln in output.splitlines():
        m = _FRAME.match(ln) or _TAIL.match(ln)
        if not m:
            continue
        try:
            rel = pathlib.Path(m.group("path")).resolve().relative_to(base)
        except (ValueError, OSError):
            continue
        out.add(f"{rel.as_posix()}:{m.group('line')}")
    return out


def gaps(domain: Iterable[str], hits: Mapping[str, Iterable[str]],
         root: str | pathlib.Path) -> tuple[list[str], list[str]]:
    """覆盖闭合的两个缺口。

    返回 `(未被任何变异撞到的判别器, 一条判别器都没撞到的变异)`。两者都空 ⇔
    判别器集合 == 「变异实际撞到的判别器」集合。

    `hits` 收的是**原始帧位置**，这里与判别器集合求交一次：包装内部那条 `raise`、以及
    「测试函数 → 辅助函数」这类中间帧都不是判别器，交集把它们滤掉。
    """
    d: set[str] = set()
    for f in domain:
        d |= {f"{f}:{n}" for n in discriminators(pathlib.Path(root) / f)}

    e: set[str] = set()
    vacuous: list[str] = []
    for label, lines in hits.items():
        got = set(lines) & d
        if not got:
            vacuous.append(label)
        e |= got

    return sorted(d - e), sorted(vacuous)


def domain_of(groups: Mapping[str, Sequence[tuple]]) -> list[str]:
    """覆盖域 = 驱动表里每条变异的靶子文件中的 `.py`（驱动自己的事实字段）。"""
    targets = {item[1] for g in groups.values() for item in g}
    return sorted(t for t in targets if isinstance(t, str) and t.endswith(".py"))


# ── 已知缺口名单（「只许减少」）───────────────────────────────────────────────
#
# **为什么名单的键不是 `文件:行号`。** 行号是**坐标**，不是身份：任何一次无关的插入/删除
# 都会让同一份名单整体平移。本仓实测过两次：`test_text_failure_messages.py` 只改了一段
# docstring（`224fd68`），L6 入口面那条静态守卫的 `assert` 就从 473 滑到 482，于是 T-3
# 的红源当场落到一个不再是判别器的行号上 —— 那条变异被记成**空转**，同时 482 凭空多出
# 一条「新缺口」。这不是理论上的担心，是名单式落地前最后一次实测踩到的。
#
# 判别器的**身份是它的源码文本**。`nth` 只在同一文件里出现**同文本**判别器时才起作用
# （实测：`assert r.status_code == 400, r.text` 在一份文件里出现 4 次、
# `assert await store.ping() is None` 出现 2 次）—— 按行号次序取第几处，插行不改变次序。

def _located(location: str) -> tuple[str, int]:
    """`"文件:行号"` → `(文件, 行号)`。"""
    rel, _, n = location.rpartition(":")
    return rel, int(n)


def gap_keys(uncovered: Iterable[str], root: str | pathlib.Path) -> dict[str, tuple[str, str, int]]:
    """未覆盖判别器的**稳定身份**：`文件:行号` → `(文件, 判别器源码, 同文本第几处)`。

    输入是 `gaps()` 的第一项，输出可放进名单作键。返的是**映射**（不是集合）—— 名单报红时
    要能指回行号，而键里故意没有行号（行号会平移）；映射就是这条回路。

    按**行号数值**排序取 `nth`，不按 `"文件:行号"` 的字典序 —— 后者在行号跨过 99→100 时
    会把两处同文本判别器的次序对调（字符串里 `"9" > "100"`），同一份名单在两个内容相同的
    树上会算出两种键。

    名单的**双向对账**不在这里：直接用 `policy_table.unexpected(现场, 名单)` 与
    `policy_table.stale_keys(名单, 现场)` —— 与本仓另外两张表（`_FORM_METADATA`、
    `ALLOWLIST`）同一套调用，三处不各写一遍。
    """
    root = pathlib.Path(root)
    cache: dict[str, dict[int, str]] = {}
    counts: dict[tuple[str, str], int] = {}
    out: dict[str, tuple[str, str, int]] = {}
    for rel, lineno in sorted((_located(loc) for loc in uncovered)):
        if rel not in cache:
            cache[rel] = discriminators(root / rel)
        snippet = cache[rel][lineno]
        nth = counts.get((rel, snippet), 0)
        counts[(rel, snippet)] = nth + 1
        out[f"{rel}:{lineno}"] = (rel, snippet, nth)
    return out


def write_artifact(path: str | pathlib.Path, driver_rel: str, domain: Sequence[str],
                   hits: Mapping[str, Sequence[str]], skipped: Sequence[str],
                   controls: Sequence[str] = ()) -> None:
    """`mutations` 只收**该红**的变异（值 = 它红在哪几行）；`controls` 收**该绿**的。

    **`controls` 不是分类标签，是把「反证」从「空转」里摘出来的唯一办法。** 一条
    「期望绿」的变异（如 X-5：把 samefile 退回字符串 `==`、同时保留 X-3 的别名装载，
    该变异下判据**仍然通过**，这才证明 X-3 的红源是 samefile 那条判断本身）红源天生为空。
    与 `mutations` 混放，它会被 `gaps()` 记成「空转变异（红了但没撞到判据）」——
    而「反证」与「空转」共用一个信号，正是本模块反复防的那副面孔。
    """
    pathlib.Path(path).write_text(
        json.dumps({"driver": driver_rel, "domain": list(domain),
                    "mutations": {k: sorted(v) for k, v in hits.items()},
                    "controls": list(controls), "skipped": list(skipped)},
                   ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8")


# ── pytest 汇总行 → 判档（三个变异驱动共用一份实现）────────────────────────────

GREEN = "green"
RED = "RED"
SKIP = "本环境不适用"
RUNAWAY = "跑不出来"


def outcome(summary: str) -> str:
    """把一次子进程 pytest 的汇总行判成四档之一：`GREEN` / `RED` / `SKIP` / `RUNAWAY`。

    **四档不是四选一的风格问题，缺哪一档都会把「没发生的事情」记成「发生了」。**

    - `RUNAWAY`：拿不到汇总行 —— 子进程崩了、或 import 期就死。原先一律落到 `GREEN`，
      于是「跑不起来」与「全部通过」共用一个信号：实测 `test_text_failure_messages.py`
      在本机 Windows 上 import 到 onnxruntime 即 access violation（退出码 0xC0000005），
      那个靶子上**每一条**变异都「绿」，而结论行照印「全部符合预期」。
    - `SKIP`：汇总行含 skipped，既不含 failed 也不含 passed-only。记成 `GREEN` 同样是
      「判据在空转」—— 判据根本没执行，绿的是空集。
    - `RED`：`error` 与 `failed` 同归。两者之间没有中间态。

    本函数只认字面，不认因果：它区分的是「跑没跑」，不是「为什么红」。
    """
    if summary == RUNAWAY:
        return RUNAWAY
    if "failed" in summary or "error" in summary:
        return RED
    if "skipped" in summary:
        return SKIP
    return GREEN


def baseline_ok(summary: str) -> bool:
    """先验基线可用的条件：既不红、也没跑不起来。

    **`SKIP` 在基线里不算坏**：那是本环境前提不成立（实测 `test_storage_ping.py` 基线就是
    「4 passed, 2 skipped」—— B-1/B-1b 的锁版 sqlite 前提），基线与变异两次都会 skip，
    红源照样说得清。红与跑不起来才是「说不清红源」。
    """
    return outcome(summary) in (GREEN, SKIP)
