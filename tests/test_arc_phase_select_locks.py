# -*- coding: utf-8 -*-
"""S1–S15（去掉纯前端 S3/S7/S10 —— 它们在 web/frontend 的 vitest 里）：单一出口的结构锁。

每条锁守一个「只许有一处」的实现点。判据是**源码文本 / AST**，不是运行时行为 ——
运行时行为由 §5 的功能用例覆盖，这里只保证「只有一份」这件事不被下一次改动悄悄破坏。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CORE = _REPO / "core"
_WEB = _REPO / "web"


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


_BOTH = [_CORE, _WEB]


# ── S1 检索过滤的物理调用点只有一处 ─────────────────────────────────────────
def test_s1_collection_query_appears_once_in_core():
    hits = _hits(_CORE, r"\bcollection\.query\(")
    assert len(hits) == 1, f"core/ 下 `collection.query(` 应恰好一处，实得 {hits}"


# ── S2 阶段切片只在 arc_view ───────────────────────────────────────────────
def test_s2_phase_slicing_only_in_arc_view():
    files = _files(_CORE, r"\.phases\[:")
    assert files <= {"core/arc_view.py"}, f"阶段切片散落在：{files}"


def test_s2_phase_range_rule_only_in_arc_view():
    """阶段号的范围规则只在 `arc_view._phase_in_range`（`valid_phase` 的范围部分）：
    别处不出现与 arc_phase 的大小比较（审计 A3）。"""
    hits = _hits(_BOTH, r"arc_phase\s*(<=|>=|<|>)|(<=|>=|<|>)\s*\w*arc_phase")
    assert {f for f, _ in hits} <= {"core/arc_view.py"}, f"阶段号范围比较散落在：{hits}"


def test_s1b_window_applicability_only_in_rag():
    """时间窗适用性（pos_schema / 指纹比对）只在 `core/rag.py`；`SessionRag` 只透传（审计 A1）。"""
    hits = _hits(_BOTH, r"\.get\((POS_SCHEMA_KEY|FINGERPRINT_KEY|\"pos_schema\"|\"content_fingerprint\")\)")
    assert {f for f, _ in hits} <= {"core/rag.py", "core/scene_indexer.py", "core/indexing_service.py"}, hits
    assert "_applicable" not in _read(_CORE / "indexing_service.py")
    assert "source_fingerprint" not in _read(_CORE / "indexing_service.py")


# ── S4 save_session 的 SQL 不碰 arc_phase ──────────────────────────────────
def test_s4_save_session_sql_has_no_arc_phase():
    offenders = []
    for store in ("postgres_store.py", "sqlite_store.py"):
        text = _read(_REPO / "storage" / store)
        m = re.search(r"async def save_session\(.*?\n(?=    async def )", text, re.S)
        assert m, f"{store} 找不到 save_session"
        if "arc_phase" in m.group(0):
            offenders.append(store)
    assert not offenders, f"save_session 动了 arc_phase 列：{offenders}"


# ── S5 不再有事后给引擎赋身份 ───────────────────────────────────────────────
def test_s5_no_after_the_fact_user_role_assignment():
    bad = _hits(_CORE, r"\.user_role\s*=") + _hits(_WEB, r"engine\.user_role\s*=")
    # 构造处的 self.user_role = ... 允许（那是构造注入）；路由的事后赋值禁止。
    bad = [(f, n) for f, n in bad if not f.endswith("chat_engine.py")]
    assert not bad, f"仍有事后给引擎赋 user_role 的地方：{bad}"


# ── S6 正文指纹只此一份实现 ────────────────────────────────────────────────
def test_s6_content_fingerprint_has_one_implementation():
    fp = _REPO / "core" / "fingerprint.py"
    assert fp.exists(), "core/fingerprint.py 不存在"
    assert "def content_fingerprint(" in _read(fp), "content_fingerprint 没定义在 fingerprint.py"
    # scene_indexer 里不该再有一份 sha256 实现（应改为调用 fingerprint.content_fingerprint）
    scene = _read(_REPO / "core" / "scene_indexer.py")
    assert "hashlib.sha256" not in scene, "scene_indexer 里还留着自己的正文指纹实现"


# ── S8 分发到阶段只出现在 dispatch ─────────────────────────────────────────
def test_s8_dispatch_is_the_only_phase_router():
    text = _read(_REPO / "core" / "card_draft.py")
    tree = ast.parse(text)
    holders = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(text, node) or ""
            if "by_phase[" in seg:
                holders.append(node.name)
    assert holders == ["dispatch"], f"`by_phase[` 出现在这些函数里：{holders}"


# ── S9「该阶段里的原文摘录」提示词片段只定义一次 ─────────────────────────────
def test_s9_phase_excerpt_prompt_fragment_single_definition():
    text = _read(_REPO / "core" / "distiller.py")
    n = text.count("该阶段里的原文摘录")
    assert n == 1, f"「该阶段里的原文摘录」在 distiller.py 出现 {n} 次（应作为常量定义一次）"


# ── S11 以 pos_schema 为条件的补位置调度只在 save_distilled_card ────────────
def test_s11_position_backfill_scheduling_only_in_save_distilled_card():
    tm = _read(_REPO / "core" / "text_manager.py")
    m = re.search(r"async def save_distilled_card\(.*?\n(?=    async def )", tm, re.S)
    assert m, "找不到 save_distilled_card"
    assert "need_positions" in m.group(0) or "has_positions" in m.group(0), (
        "save_distilled_card 里没有补位置调度")


# ── S12 起点可用性的判定只此一处（定义位置由本段 S7 钉在 schema）──────────────
def test_s12_has_positions_callers_are_allowlisted():
    # 定义唯一性（挪到 CharacterArc.has_positions）由 test_arc_phase_fields_locks.py 的 S7 守；
    # 本条只守「谁调用它」：调用不是重复判定，别处**自己写**齐全/递增检查才违反。
    callers = _files(_BOTH, r"\bhas_positions\(")
    # arc_view 是阶段的判断方（检索上界），text_manager / distiller 是①就有的两个调用方，
    # card_out 是出卡这一处（B4：`selectable` 现算）。
    assert callers <= {"core/schema.py", "core/arc_view.py",
                       "core/text_manager.py", "core/distiller.py",
                       "core/card_out.py"}, (
        f"has_positions 的调用方超出允许名单，实得 {callers}")


# ── S13 不用 text.find 回找片段位置 ─────────────────────────────────────────
def test_s13_no_find_backtrack_for_positions():
    # 只看产位置的模块；core 别处有合法的 .find（JSON 解析、并查集），不在此列。
    owners = {"core/scene_indexer.py", "core/rag.py", "core/quotes.py", "core/arc_view.py"}
    bad = [h for h in _hits(_CORE, r"\.find\(") if h[0] in owners]
    assert not bad, f"切分/坐标模块里出现 .find( 回找（重复段落会找错）：{bad}"


# ── S14 project_card 只在一份允许名单里调用 ─────────────────────────────────
def test_s14_project_card_called_only_in_the_allowlist():
    """调用点 = 构造（chat_engine）+ 拼 prompt 的入口（opening / distill 苏醒 /
    text_manager 变体）+ 整卡读者（export / market 回复）。②之后新增的入口一律走
    `project_opening_prompt` 或先投影再拼，别在别处又开一处 —— 名单外即红。"""
    av = _REPO / "core" / "arc_view.py"
    assert av.exists() and "def project_card(" in _read(av), (
        "project_card 未定义在 core/arc_view.py")
    files = _files(_BOTH, r"\bproject_card\(")
    allowed = {"core/arc_view.py", "core/chat_engine.py", "core/opening.py",
               "core/export.py", "core/text_manager.py",
               "web/routers/distill.py", "web/routers/market.py"}
    assert files <= allowed, f"project_card 散落到了：{files - allowed}"


# ── S15 边界说明文案常量只定义一次 ─────────────────────────────────────────
def test_s15_boundary_notice_defined_once():
    hit = "你只知道到此刻为止发生过的事"
    files = [f for f, _ in _hits(_BOTH, re.escape(hit))]
    assert len(files) == 1, f"边界说明文案出现在多处：{files}"
