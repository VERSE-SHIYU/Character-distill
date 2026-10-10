# -*- coding: utf-8 -*-
"""§6.3 结构锁 S1–S12：每条锁钉一个「只许有一处」的实现点。

判据是**源码文本 / AST**，不是运行时行为 —— 行为由 §6.1/§6.2 覆盖，这里只保证
「只有一份」不被下一次改动悄悄破坏。先红后绿：本段开始时这些点都还不存在。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CORE = _REPO / "core"
_WEB = _REPO / "web"
_BOTH = [_CORE, _WEB]


def _py(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _hits(root: Path | list[Path], pattern: str) -> list[tuple[str, int]]:
    rx = re.compile(pattern)
    roots = root if isinstance(root, list) else [root]
    out = []
    for r in roots:
        for p in _py(r):
            for n, line in enumerate(_read(p).splitlines(), 1):
                if rx.search(line):
                    out.append((str(p.relative_to(_REPO)).replace("\\", "/"), n))
    return out


def _files(root: Path | list[Path], pattern: str) -> set[str]:
    return {f for f, _ in _hits(root, pattern)}


def _funcs(path: Path) -> dict[str, str]:
    text = _read(path)
    return {
        n.name: (ast.get_source_segment(text, n) or "")
        for n in ast.walk(ast.parse(text))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _kwarg_sources(path: Path, method: str, kwarg: str) -> list[str]:
    """源码里所有 `self.<method>(...)` 调用中 `kwarg=` 实参的源码片段（AST，不看注释）。"""
    text = _read(path)
    out: list[str] = []
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == method:
            for kw in n.keywords:
                if kw.arg == kwarg:
                    out.append((ast.get_source_segment(text, kw.value) or "").strip())
    return out


def _imports(path: Path) -> set[str]:
    """导入的**子模块名**，相对 `core` 归一：`core.distiller` → `distiller`、
    `core.adapters.x` → `adapters`；仓外模块取首段（`pydash`）。

    不归一就会塌成 `core` —— `from core.distiller import …` 的首段是 `core`，导入方向锁
    看着全绿其实一条都没拦（MA22 打不红就是这么来的）。
    """
    mods: set[str] = set()
    for n in ast.walk(ast.parse(_read(path))):
        if isinstance(n, ast.Import):
            names = {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names = {n.module or ""}
        else:
            continue
        for name in names:
            name = name[5:] if name.startswith("core.") else name
            head = name.split(".")[0]
            if head:
                mods.add(head)
    return mods - {"__future__"}


# ── S1 登记表 = `CharacterCard` 叶子全集（与 U1 同源，这里只锁「定义在 card_layers」） ──
def test_s1_registry_lives_in_card_layers_only():
    defs = _hits(_CORE, r"^REGISTRY\s*(:|=)")
    assert [f for f, _ in defs] == ["core/card_layers.py"], f"REGISTRY 应只在 card_layers 定义一次：{defs}"


# ── S2 `project_card` 不出现具体字段名分支（custom 除外） ──────────────────────
def test_s2_project_card_has_no_per_field_branch():
    from core.card_layers import REGISTRY

    custom = {"axis", "phases", "relationships", "first_message", "situation_behaviors"}
    generic = {p.split(".")[-1] for p, s in REGISTRY.items() if s.layer in ("state", "experience")}
    src = _funcs(_CORE / "arc_view.py")["project_card"]
    offenders = sorted(n for n in generic if n not in custom and n in src)
    assert not offenders, f"project_card 里仍逐字段写死了状态/经历字段：{offenders}"


# ── S3 拼人设 prompt 的模块只读投影卡 ─────────────────────────────────────────
def test_s3_prompt_builders_go_through_project_card():
    assert "project_card(" in _read(_WEB / "routers" / "market.py"), "market.py 未走 project_card"
    assert "project_card(" in _read(_CORE / "opening.py"), "opening.py 未走 project_card"


def test_s3b_projected_card_constructed_only_in_arc_view():
    files = _files(_BOTH, r"\bProjectedCard\(")
    assert files <= {"core/arc_view.py"}, f"ProjectedCard(...) 应只在 arc_view 构造：{files}"
    assert "ProjectedCard" in _read(_CORE / "context_engine.py"), "ContextEngine 入口未收 ProjectedCard"
    assert "ProjectedCard" in _read(_CORE / "opening.py"), "opening 入口未收 ProjectedCard"


# ── S4 描述规则、稳定类规则常量各一次 ────────────────────────────────────────
def test_s4_rule_constants_defined_once():
    text = _read(_CORE / "distiller.py")
    for name in ("_PHASE_EXCERPT_RULE", "_PHASE_DESCRIPTION_RULE", "_STABLE_FIELD_RULE",
                 "_EVIDENCE_RULE", "_PHASE_VERBATIM_RULE"):
        n = len(re.findall(rf"^{name}\s*(?::[^=]+)?=", text, re.M))
        assert n == 1, f"{name} 在 distiller.py 定义 {n} 次（应恰好一次）"


# ── S5 `distiller.py` 无写死 "G5" 依赖 ───────────────────────────────────────
def test_s5_no_hardcoded_g5_dependency():
    text = _read(_CORE / "distiller.py")
    assert not re.search(r"""!=\s*["']G5["']""", text), "还留着写死的 G5 特判"
    assert "PHASE_DEPENDENT_GROUPS" in text, "依赖组未改由登记表推导"


# ── S6 `by_phase[` 只在 dispatch ───────────────────────────────────────────
def test_s6_by_phase_index_only_in_dispatch():
    text = _read(_CORE / "card_draft.py")
    holders = [n for n, s in _funcs(_CORE / "card_draft.py").items() if "by_phase[" in s]
    assert holders == ["dispatch"], f"`by_phase[` 出现在这些函数里：{holders}"


def test_s6b_dispatch_is_the_only_dispatcher():
    """分发只有 `dispatch` 一个（B1）：列表与单值走同一个函数，`kind` 决定格子容量。"""
    names = [n for n in _funcs(_CORE / "card_draft.py") if n.startswith("dispatch")]
    assert names == ["dispatch"], f"card_draft 里分发函数应只有 dispatch：{names}"


# ── S7 起点判定只在 `CharacterArc.has_positions` ────────────────────────────
def test_s7_has_positions_only_in_schema():
    """起点判据只在 schema 一处实现（`_positions_usable`）。

    `has_positions` 有两个入口（`CharacterArc` 与只读起点的 `ArcPositions`，R3），都只能在
    schema 里、且都只是调用 `_positions_usable` —— 判据本身不许出现第二份。
    """
    defs = _hits(_CORE, r"def has_positions\(")
    assert {f for f, _ in defs} == {"core/schema.py"}, f"has_positions 应只在 schema 定义：{defs}"
    assert "def has_positions(" not in _read(_CORE / "arc_view.py"), "arc_view 里还留着 has_positions"
    schema = _read(_CORE / "schema.py")
    assert len(re.findall(r"^def _positions_usable\(", schema, re.M)) == 1, "判据实现不是恰好一处"
    bodies = re.findall(r"def has_positions\(self\)[^\n]*\n(?:\s+\"\"\"[\s\S]*?\"\"\"\n)?(\s+[^\n]+)", schema)
    assert len(bodies) == len(defs) and all("_positions_usable(" in b for b in bodies), (
        f"has_positions 不是直接调用 _positions_usable：{bodies}")


# ── S8 关系分批只有 `_relationships_batched` 一处、批大小常量一次 ─────────────
def test_s8_batching_single_entry_and_batch_size_constant():
    d = _read(_CORE / "distiller.py")
    assert len(re.findall(r"def _relationships_batched\b", d)) == 1, "distiller 里 _relationships_batched 不是恰好一处"
    rb = _read(_CORE / "relationship_batch.py")
    assert len(re.findall(r"^REL_BATCH_SIZE\s*(?::[^=]+)?=", rb, re.M)) == 1, "REL_BATCH_SIZE 不是恰好一处"
    assert "REL_BATCH_SIZE" in d, "distiller 未从 relationship_batch 取批大小"


# ── S9 分批调用只经 `_collect_stream` ──────────────────────────────────────
def _self_calls(path: Path, func: str) -> set[str]:
    """函数体里 `self.<name>(...)` 实际调用的方法名（AST，**不看注释/文档串**）——

    文本子串会被 docstring 里那句「每批走 `_collect_stream`」满足，变异把调用换成非流式
    也照绿（MA19 打不红就是这么来的）。
    """
    out: set[str] = set()
    for n in ast.walk(ast.parse(_read(path))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func:
            for c in ast.walk(n):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and isinstance(c.func.value, ast.Name) and c.func.value.id == "self"):
                    out.add(c.func.attr)
    return out


def test_s9_batches_go_through_collect_stream():
    calls = _self_calls(_CORE / "distiller.py", "_relationships_batched")
    assert "_collect_stream" in calls, "分批未走 _collect_stream（流式）"


# ── S10 审核与守卫共用一个遍历函数 ──────────────────────────────────────────
def test_s10_review_and_guard_share_one_walker():
    defs = _hits(_CORE, r"def iter_texts\(")
    assert [f for f, _ in defs] == ["core/moderation/card_text.py"], f"共享遍历未唯一：{defs}"
    for f in ("core/moderation/auto_review.py", "core/moderation/card_guard.py"):
        assert "iter_texts" in _read(_REPO / f), f"{f} 未用共享遍历"


# ── S11 导入方向（§4.0）：AST 扫描各模块导入，违反即红 ───────────────────────
def test_s11_import_direction():
    cl = _imports(_CORE / "card_layers.py")
    assert cl <= {"schema", "pydash", "typing"}, f"card_layers 导入了越界模块：{cl}"
    assert "schema" in cl or "pydash" in cl

    rv = _imports(_CORE / "arc_view.py")
    assert not (rv & {"distiller", "adapters", "web", "storage"}), f"arc_view 导入越界：{rv}"

    rb = _imports(_CORE / "relationship_batch.py")
    assert not (rb & {"distiller", "adapters", "web"}), f"relationship_batch 导入越界：{rb}"

    op = _imports(_CORE / "opening.py")
    assert not (op & {"web", "storage", "distiller"}), f"opening 导入越界：{op}"

    # `fingerprint` 是只依赖标准库的叶子模块：卡的 revision 复用正文指纹那一处哈希（§13），
    # 不在出卡模块里另写一份 hashlib。
    co = _imports(_CORE / "card_out.py")
    assert co <= {"schema", "fingerprint", "json", "typing"}, (
        f"card_out 导入了越界模块（§4.0 只可导入 core.schema 与叶子模块 core.fingerprint）：{co}")


# ── S12 开场白 / 苏醒台词提示词只在 `core/opening.py` ─────────────────────────
def test_s12_opening_prompts_only_in_opening_module():
    markers = ("放进当下场景", "第一眼认出眼前人", "重新说一句意思相近但措辞不同")
    src_opening = _read(_CORE / "opening.py")
    for m in markers:
        assert m in src_opening, f"opening.py 缺提示词特征串：{m}"
    for f in (_WEB / "routers" / "distill.py", _CORE / "text_manager.py"):
        text = _read(f)
        for m in markers:
            assert m not in text, f"{f.name} 里还有内联开场白提示词：{m}"
    # 两处开场白（新会话 + 开场变体）都经 core/opening.py（§4.6）—— 变体若又内联回
    # text_manager，这里点名（MA23）。
    assert "build_variation_prompt(" in _read(_CORE / "text_manager.py"), (
        "text_manager 的开场变体未走 core.opening.build_variation_prompt")


# ── S13 关系生成口径（RELATIONSHIP_RULES）只在 relationship_batch 一处（B7） ──
def test_s13_relationship_rules_defined_once():
    """关系生成的口径只此一处，且 `_batch_prompt` 确实引用它（不是死常量）。

    关系规则（单向视角 / note 是注入立场 / 只写态度变了的阶段、第一条写开始有交集的阶段 /
    phase 0 / quote 是原文摘录 / 每条阶段态度都写 note / 顶层写最初的关系）讲的是**关系怎么写**；主调用维度 F 的「只出名单」讲的是**这一步先别写**，两步
    的两句话。规则若也内联进 distiller 的维度说明，就成了「同一条规则两处」，改一处漏一处。
    """
    defs = _hits(_CORE, r"^RELATIONSHIP_RULES\s*(?::[^=]+)?=")
    assert [f for f, _ in defs] == ["core/relationship_batch.py"], (
        f"RELATIONSHIP_RULES 应只在 relationship_batch 定义一次：{defs}")
    for marker in ("单向视角", "只写态度变了的阶段"):
        files = _files(_CORE, marker)
        assert files <= {"core/relationship_batch.py"}, f"关系口径「{marker}」在别处也有：{files}"
    assert "RELATIONSHIP_RULES" in _funcs(_CORE / "relationship_batch.py")["_batch_prompt"], (
        "_batch_prompt 没引用 RELATIONSHIP_RULES（常量成了死代码）")


# ── S14 关系分批的前缀只能由共享前缀函数产出（B2 提示词）────────────────────
def test_s14_batch_prefix_only_from_shared_prefix():
    """分批前缀 = 共享前缀（`book_prefix` 正文 / `format_prompt_shared` 组共享段）。

    传出主调用的系统提示会把维度 F 的「不要写关系详情」带进「给我关系详情」的这一步 ——
    前缀只能是**不含本步指令**的那一段；本锁钉住「只有这两个产出者」。
    """
    prefixes = _kwarg_sources(_CORE / "distiller.py", "_relationships_batched", "prefix")
    assert prefixes, "没找到任何 _relationships_batched 的 prefix 实参（判据失去意义）"
    bad = [p for p in prefixes
           if not (p.startswith("book_prefix(") or p.startswith("format_prompt_shared("))]
    assert not bad, f"分批前缀不是共享前缀函数产出的：{bad}"


def test_s14b_shared_prefix_is_a_real_shared_prefix():
    """组共享前缀 = 每个组提示词共同的开头，且不含本步指令（维度 F 的「只出名单」）。

    只锁「存在一个共享前缀函数」不够：它必须**真的是**各组提示词的前缀（缓存才命中），
    且不含组专属维度 —— 退回整段 `format_prompt_after()` 就不是任何组的前缀（MB7）。
    """
    from core.distiller import (
        DISTILL_PROMPT_BEFORE_NAME, format_prompt_after, format_prompt_shared, book_prefix)
    from core.schema import FORMAT_GROUPS

    shared = format_prompt_shared("甲")
    for g in FORMAT_GROUPS:
        full = DISTILL_PROMPT_BEFORE_NAME + "甲" + format_prompt_after(g)
        assert full.startswith(shared), f"组共享前缀不是 {g} 提示词的前缀"
    assert "只出名单" not in shared, "组共享前缀带了维度 F 的「只出名单」"
    assert "只出名单" not in book_prefix("正文"), "book_prefix 带了维度 F 的「只出名单」"


# ── S15 出卡只有一处：派生 `selectable` 不落库，出卡单点现算（B4）────────────
def _callers(path: Path, func: str) -> set[str]:
    """调用 `func(...)` 的**所属函数名**集合（AST；模块级调用记为 `<module>`）。

    不看注释/文档串 —— 文本子串会被「出卡这一处」这类说明满足。
    """
    tree = ast.parse(_read(path))
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for c in ast.walk(node):
                owner.setdefault(id(c), node.name)
    return {owner.get(id(n), "<module>")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func}


def _returns_result_of(path: Path, method: str) -> set[str]:
    """函数名集合：函数体里 `x = await <obj>.<method>(...)`，且某个 `return` 用到了 `x`（AST）。"""
    out: set[str] = set()
    for fn in ast.walk(ast.parse(_read(path))):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and isinstance(n.value, ast.Await) and isinstance(n.value.value, ast.Call)
                 and isinstance(n.value.value.func, ast.Attribute)
                 and n.value.value.func.attr == method
                 for t in n.targets if isinstance(t, ast.Name)}
        used = {x.id for r in ast.walk(fn) if isinstance(r, ast.Return) and r.value is not None
                for x in ast.walk(r.value) if isinstance(x, ast.Name)}
        if bound & used:
            out.add(fn.name)
    return out


def test_s15_out_card_is_the_only_card_out():
    """出卡只有 `core/card_out.py::out_card` 一处；返回卡片给 store 的接口都经它（B4）。

    `selectable` 是派生值（起点齐全 + 指纹），**不随卡落库**：模型上不再有 `computed_field`，
    否则存库那刻算一遍写进 `card_json` 就会过期（旧卡没这个键；位置是后台作业补的，补完那张
    卡存下的仍是 false）。出卡这一处按当前 phases / 指纹现算。市场、群聊不走开聊按钮，不接。
    """
    # 1) 定义只有一处
    defs = _hits(_CORE, r"^def out_card\(")
    assert [f for f, _ in defs] == ["core/card_out.py"], f"out_card 应只在 card_out 定义一次：{defs}"

    # 2) 模型不再把派生值当字段序列化
    schema = _read(_CORE / "schema.py")
    assert "computed_field" not in schema, "schema 里还留着 computed_field（派生值会落库）"
    assert not re.search(r"\bdef selectable\b", schema), "schema 里还留着 selectable"

    # 3) 调用点 = 返回的卡会写进 store、被开聊按钮读到的接口：两个开聊列表 + 编辑保存 + 挪动
    #    （spec arc-phase-unlocated §6：本意是「流到开聊按钮的卡都经 out_card」，不是「只两处」）。
    #    机械判据：distill 路由里凡是**把 `storage.update_card` 的结果 return 出去**的函数，
    #    都必须在集合里（后台任务只写不返回，不算）。
    #    「待补对话示例」的两个出口（重新找、关掉，docs/specs/examples-pending.md）同样把卡返回给
    #    store，同样经 out_card。
    callers = _callers(_WEB / "routers" / "distill.py", "out_card")
    assert callers == {"list_cards", "list_standalone_cards", "update_card",
                       "move_unlocated_item", "refind_dialogue_examples",
                       "dismiss_examples_pending"}, f"out_card 调用点不对：{callers}"
    returning = _returns_result_of(_WEB / "routers" / "distill.py", "update_card")
    assert returning == {"update_card", "move_unlocated_item",
                         "refind_dialogue_examples"}, f"返回更新后卡片的接口变了：{returning}"
    assert returning <= callers, f"返回更新后卡片却没走 out_card：{returning - callers}"
    extra = _files(_BOTH, r"\bout_card\(") - {"core/card_out.py", "web/routers/distill.py"}
    assert not extra, f"out_card 在别处也被调/定义：{extra}"

    # 4) `"selectable"` 字面只在出卡这一处（core/web 的 .py）
    lit = _files(_BOTH, r"""["']selectable["']""")
    assert lit <= {"core/card_out.py"}, f"`selectable` 字面逸出出卡模块：{lit}"


# ── S16 字段名只在登记表登记一次；阶段表头只由 `phase_header` 产出（B5、B6）────
def test_s16_field_labels_only_in_registry():
    """字段中文名的唯一来源是登记表 ``FieldSpec.label``：非空、互不相同。

    导出正文经 ``card_outline`` 遍历登记表取名字（不自己写字段名）；阶段表头一律由
    ``phase_header`` 产出（内联的 ``f"阶段 {i}·…"`` 已清）。
    """
    from core.card_layers import REGISTRY

    labels = [s.label for s in REGISTRY.values()]
    assert all(lb.strip() for lb in labels), "有字段没登记中文名（都是后端唯一来源的一部分）"
    dupes = sorted({lb for lb in labels if labels.count(lb) > 1})
    assert not dupes, f"字段中文名有重复：{dupes}"

    # 表头函数只定义一次
    defs = _hits(_CORE, r"^def phase_header\(")
    assert [f for f, _ in defs] == ["core/arc_view.py"], f"phase_header 定义处不唯一：{defs}"

    # 四个产表头的模块都经它；内联的「阶段 i·…」已清
    for rel in ("core/export.py", "core/context_engine.py",
                "core/distiller.py", "core/opening.py"):
        assert "phase_header" in _read(_REPO / rel), f"{rel} 的阶段表头未走 phase_header"
    inline = [h for h in _hits(_CORE, r'f"阶段 \{[^"]*·')
              if h[0] != "core/arc_view.py"]          # arc_view 是 phase_header 本体
    assert not inline, f"还有内联的阶段表头（未走 phase_header）：{inline}"

    # 导出正文经 card_outline 遍历登记表
    assert "card_outline" in _read(_CORE / "export.py"), "导出未走 card_outline"

    # `ArcView` 不再自带 memories（记忆只在投影卡上算一次，别算两处）
    tree = ast.parse(_read(_CORE / "arc_view.py"))
    arcview = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "ArcView")
    fields = {n.target.id for n in arcview.body if isinstance(n, ast.AnnAssign)}
    assert "memories" not in fields, "ArcView 又带上了 memories（同一份记忆算两处）"
