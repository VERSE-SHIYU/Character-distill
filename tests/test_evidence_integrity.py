"""证据产物契约锁 —— 引用闭合 / 条目合法 / status 三值 / 白名单注册 / 落点不可覆盖。

锁的是**机制**而不是这一批数字：正文里任何 ``ev:<id>`` 都必须解析到清单条目，任何条目都必须
满足它那一档 ``status`` 的必填字段。新增一个 ``ev:`` 引用却忘了登记，或者标了 ``verified``
却没有入库产物，这里直接红。

为什么值得单独锁（缺陷 14）：文档数字的「可追溯」以前靠作者记性维持，靠不住的地方在于
**漏一次没有任何东西报警**。锁把「忘了」变成「红了」。契约见 ``docs/evidence/README.md``。
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERF = ROOT / "tests" / "perf"
MANIFEST = ROOT / "docs" / "evidence" / "manifest.json"

# id 形态：小写字母数字起步，允许 . _ -。README 里的占位写法 `ev:<id>` 刻意不匹配
# （`<` 不在字符类里），否则文档讲语法本身就会被当成引用。
EV_RE = re.compile(r"ev:([a-z0-9][a-z0-9._-]*)")

_REQUIRED = {
    "verified": ("artifact", "script", "script_role", "reproduce", "assertions", "derived",
                 "non_repo_paths"),
    "runtime-measured": ("env", "measured_at", "notes", "non_repo_paths"),
    "unverifiable": ("measured_at", "env", "notes", "non_repo_paths"),
}
_STATUSES = ("verified", "runtime-measured", "unverifiable")
_SCRIPT_ROLES = ("producer", "corroborating")

# ── L2 指称闭合（缺陷 14 v3 §四）：字段值必须解析回仓库里的实体，不能只校验形状 ──
# `producer` 的定义是「跑它能重生成这份产物」，脚本不在库里这个定义当场不成立 ——
# 而在此之前没有任何断言看 `script` 一眼。
_SHA_SENTINELS = ("unknown(scratch)",)
# reproduce 允许引用的运行期输入（语料库等）—— 复现命令读它没问题，只是它不入库。
# 只剩 `data/`：其余非仓库路径一律走 `non_repo_paths` 逐条声明（前缀猜测是启发式，
# 新增一个 gitignored 目录就漏）。
_RUNTIME_OK_PREFIXES = ("data/",)
_PATH_RE = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)*[\w.-]+"
    r"\.(?:py|json|md|sh|txt|ya?ml|cjs|js|ts|tsx|sql|toml|ini|cfg)(?![\w])")

# ── L3 数值闭合（缺陷 14 v3 §五）：claim 里的数字必须落在入库产物上 ──
# 只认独立的数字串：`9d2a9e4` 是 commit 的一部分，不是量值。
_NUM_RE = re.compile(r"(?<![0-9A-Za-z_])\d+(?![0-9A-Za-z_])")

# ── M0 提取器反空过（缺陷 14 v4 §二之二）────────────────────────────────────
# `_path_tokens` / `_numbers` 是整套判据里**唯一不可能靠「声明」消除**的启发式：没被提取到的
# 东西，既不会被查、也不会被要求声明 —— 你不能声明你没注意到的东西。这不是能修掉的病，是真
# 边界。但必须防它退化成空过，所以把识别面钉成语料：提取器将来放宽或收紧，这组语料就是它的
# 回归锁。**判据写在这里，不写在 README 里** —— 识别不到的形态就是这道门的真实边界，
# 摆出来让人看见，而不是靠注释声明「大概能认」。
_PATH_CORPUS = {
    "反引号包裹": ("见 `tests/perf/x.py` 处的实现", {"tests/perf/x.py"}),
    "行内代码块": ("调用 `tests/perf/map_len_probe.py` 得到", {"tests/perf/map_len_probe.py"}),
    "中文标点紧邻": ("见 tests/perf/x.py，随后按脚本重跑", {"tests/perf/x.py"}),
    "带行号后缀": ("AGENTS.md:349 那一段", {"AGENTS.md"}),
    "相对路径": ("改动 tests/test_evidence_integrity.py 时", {"tests/test_evidence_integrity.py"}),
    "证据产物形态": ("产物落在 docs/evidence/incomplete-v5.json",
                     {"docs/evidence/incomplete-v5.json"}),
}
# 语料形态清单**独立一份**：只靠语料自身，删掉一行没人红（少查一行照样绿）。
_PATH_SHAPES = frozenset({
    "反引号包裹", "行内代码块", "中文标点紧邻", "带行号后缀", "相对路径", "证据产物形态",
})
# 已知不识别（边界，不是待修）：`<id>` 占位符不是文件名字符。清单里的真实写法是把 id 写实。
_PATH_KNOWN_MISSES = {"docs/evidence/<id>.json": "占位符形态（`<` 不是文件名字符）"}

_NUM_CORPUS = {
    "等号赋值": ("n=14 条记录", {14}),
    "字段名后跟量": ("p50 1245 与 1446 两档", {1245, 1446}),
    "裸整数": ("上限 8192 未触顶", {8192}),
    "百分号": ("占 94% 的样本", {94}),
    "斜杠分隔": ("12441/12413 两个字符数", {12441, 12413}),
}
# 同样独立一份：字段名里的数字**不算量值**（`p50` 抽成 50 会让 claim 长出幻影数字）。
_NUM_SHAPES = frozenset({"等号赋值", "字段名后跟量", "裸整数", "百分号", "斜杠分隔"})

sys.path.insert(0, str(PERF))
import evidence_writer  # noqa: E402


def _manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"], cwd=ROOT, capture_output=True, check=True)
    return [ROOT / p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def _references() -> list[tuple[str, str]]:
    """全仓正文里的 (文件, id) 引用对。"""
    refs: list[tuple[str, str]] = []
    for path in _tracked_markdown():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in EV_RE.finditer(text):
            refs.append((path.relative_to(ROOT).as_posix(), m.group(1)))
    return refs


def _is_tracked(rel: str) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel], cwd=ROOT, capture_output=True)
    return r.returncode == 0


def _path_tokens(text: str) -> set[str]:
    """正文里长得像仓库文件路径的 token（带已知后缀、至少一段目录或裸文件名）。"""
    return {m.group(0) for m in _PATH_RE.finditer(text or "")}


def _numbers(text: str) -> set[int]:
    return {int(x) for x in _NUM_RE.findall(text or "")}


def _resolve(obj, path: str):
    """`summary[1].out_tokens_p50` 形式的取值路径 —— 点取键、方括号取下标。"""
    cur = obj
    for tok in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        cur = cur[int(tok[1:-1])] if tok.startswith("[") else cur[tok]
    return cur


class TestReferenceClosure:
    def test_the_scan_actually_reads_the_docs(self):
        """防「绿得没意义」：扫描面本身先得是活的。"""
        docs = {p.relative_to(ROOT).as_posix() for p in _tracked_markdown()}
        assert len(docs) >= 15, f"只扫到 {len(docs)} 个 tracked .md —— 扫描面坏了"
        assert {"AGENTS.md", "docs/engineering-evidence.md"} <= docs

    def test_every_reference_resolves_to_a_manifest_entry(self):
        ids = {e["id"] for e in _manifest()}
        orphans = [(f, i) for f, i in _references() if i not in ids]
        assert not orphans, (
            f"正文引用了清单里没有的证据 id：{orphans}。"
            "要么登记条目（跑探针走 evidence_writer），要么删掉引用。")

    def test_every_reference_has_a_registered_whitelist(self):
        missing = sorted({i for _f, i in _references() if i not in evidence_writer._ALLOWED_BY_ID})
        assert not missing, (
            f"证据 id 未在 evidence_writer._ALLOWED_BY_ID 注册：{missing}。"
            "没有注册就没有白名单，脱敏会退回「记得删」。")


class TestExtractorPins:
    """M0 —— 提取器必须认得住已知形态，否则它的「没报错」是空过。

    `_path_tokens` / `_numbers` 认不出的 token，既不进「必须解析到仓库」的判据，也不进
    「必须被声明」的判据。于是提取器一旦悄悄变窄，整套闭合会**整体空过**而全绿。
    """

    def test_path_extractor_recognizes_the_pinned_shapes(self):
        assert set(_PATH_CORPUS) == _PATH_SHAPES, (
            f"路径语料形态被增删：{sorted(set(_PATH_CORPUS) ^ _PATH_SHAPES)}。"
            "删一行等于悄悄缩小识别面 —— 要么补回，要么同步改 _PATH_SHAPES 并说明理由。")
        bad = [(name, text, want, _path_tokens(text))
               for name, (text, want) in _PATH_CORPUS.items()
               if _path_tokens(text) != want]
        assert not bad, (
            f"路径提取器认不出这些形态（名字, 语料, 期望, 实际）：{bad}。"
            "空过点在这里：认不出的路径不会被查、也不会被要求声明。")
        leaked = sorted(t for t in _PATH_KNOWN_MISSES if _path_tokens(t))
        assert not leaked, (
            f"原以为是边界、现在能认了：{leaked}。这是好事 —— 但把语料与 "
            "_PATH_KNOWN_MISSES 同步更新，别让边界描述漂移。")

    def test_number_extractor_recognizes_the_pinned_shapes(self):
        assert set(_NUM_CORPUS) == _NUM_SHAPES, (
            f"数字语料形态被增删：{sorted(set(_NUM_CORPUS) ^ _NUM_SHAPES)}。")
        bad = [(name, text, want, _numbers(text))
               for name, (text, want) in _NUM_CORPUS.items()
               if _numbers(text) != want]
        assert not bad, (
            f"数字提取器认不出这些形态（名字, 语料, 期望, 实际）：{bad}。"
            "注意 `p50` 这类字段名里的数字**不该**被抽成量值 —— 那会让 claim 长出幻影数字。")


class TestManifestEntries:
    def test_status_is_exactly_one_of_three(self):
        bad = [(e.get("id"), e.get("status")) for e in _manifest()
               if e.get("status") not in _STATUSES]
        assert not bad, (
            f"status 只允许 {_STATUSES}，这些不是：{bad}。"
            "加第四档等于让 status 同时描述可追溯性与核对结论 —— 见 docs/evidence/README.md。")

    def test_required_fields_present_and_non_empty(self):
        problems = []
        for e in _manifest():
            for field in ("id", "claim", "status") + _REQUIRED.get(e.get("status"), ()):
                v = e.get(field)
                if v is None or (isinstance(v, str) and not v.strip()):
                    problems.append((e.get("id"), e.get("status"), field))
        assert not problems, f"条目缺必填字段（id, status, 字段）：{problems}"

    def test_verified_artifact_is_git_tracked(self):
        missing = [e["id"] for e in _manifest()
                   if e.get("status") == "verified" and not _is_tracked(e.get("artifact") or "")]
        assert not missing, (
            f"标了 verified 但产物没被 git 命中：{missing}。"
            "跑完探针要把 docs/evidence/<id>.json 一并提交，否则就是「该转正没转」。")

    def test_other_statuses_carry_no_artifact(self):
        bad = [e["id"] for e in _manifest()
               if e.get("status") in ("runtime-measured", "unverifiable")
               and e.get("artifact") is not None]
        assert not bad, (
            f"runtime-measured / unverifiable 的 artifact 必须为 null：{bad}。"
            "有产物却标这两档 = 该转正没转。")

    def test_artifact_lives_under_docs_evidence(self):
        bad = [(e.get("id"), e.get("artifact")) for e in _manifest()
               if e.get("artifact") is not None
               and not str(e["artifact"]).startswith("docs/evidence/")]
        assert not bad, f"产物必须落在 docs/evidence/ 下：{bad}"

    def test_script_role_is_declared_and_legal(self):
        """`script` 有两种语义（产出这份产物 / 只佐证同一断言），必须逐条表态。

        不给这一条，区别就只活在散文里：下一个人照抄一条 `verified + script 指向别的文件`
        的形态，就填出一条**真不可复现**的条目，而锁全绿。
        """
        bad = []
        for e in _manifest():
            role = e.get("script_role")
            if e.get("status") == "verified":
                if role not in _SCRIPT_ROLES:
                    bad.append((e["id"], role))
            elif role is not None:
                bad.append((e["id"], role))
        assert not bad, (
            f"script_role 对 verified 必须是 {_SCRIPT_ROLES} 之一、对其余两档必须是 null：{bad}")

    def test_corroborating_script_must_be_explained(self):
        """script 不是产出脚本时，`notes` 必须说明产出脚本是谁、为什么没入库。"""
        bad = [e["id"] for e in _manifest()
               if e.get("script_role") == "corroborating" and not (e.get("notes") or "").strip()]
        assert not bad, (
            f"script_role=corroborating 却没有 notes 交代产出脚本：{bad}。"
            "「为什么这个 script 不产这份产物」不能只靠读者猜。")

    def test_every_entry_has_a_registered_whitelist(self):
        missing = sorted({e["id"] for e in _manifest()
                          if e["id"] not in evidence_writer._ALLOWED_BY_ID})
        assert not missing, f"清单条目未在 _ALLOWED_BY_ID 注册白名单：{missing}"

    # ── L2：字段值解析回仓库实体（形状对 ≠ 指得着）──────────────────────────

    def test_script_is_tracked(self):
        """`producer` 的定义蕴含「脚本在库」；`corroborating` 指向的佐证用例同样得在库。"""
        bad = [(e["id"], e["script"]) for e in _manifest()
               if e.get("script") and not _is_tracked(e["script"])]
        assert not bad, (
            f"script 指向的文件不在 git 管理下：{bad}。"
            "producer 的定义是「跑它能重生成这份产物」—— 脚本不在库，这个定义当场不成立。")

    def test_reproduce_paths_resolve(self):
        """复现命令里的仓内路径必须存在，否则读者粘进终端就是 No such file。

        运行期输入（`data/` 语料库等）与非仓库前缀放行 —— 复现命令读它们没问题。
        """
        bad = []
        for e in _manifest():
            for tok in _path_tokens(e.get("reproduce") or ""):
                if _is_tracked(tok) or tok.startswith(_RUNTIME_OK_PREFIXES):
                    continue
                bad.append((e["id"], tok))
        assert not bad, (
            f"reproduce 引用了库里没有的路径：{bad}。"
            "复现命令是本清单的对外承诺，指向空文件等于没承诺。")

    def test_code_sha_resolves(self):
        """`code_sha` 要么解得开一个 commit，要么精确等于哨兵值。

        哨兵锁死成枚举：不锁死就会出现第二种写法，然后两种都不被校验。
        先例 `incomplete-v5` 的 `unknown(scratch)` 是**合规**的 —— 产出时点只能界在一个
        commit 窗口内、落不到唯一点，如实记。本条保护的正是这种诚实标注不被随手改成
        一个编造的 sha（复算的人 checkout 到错的点，然后得出「数字对不上」）。

        **依赖完整 git 历史**：`git cat-file -t <sha>` 只能解析出**克隆里实际存在**的
        对象。CI（`.github/workflows/build.yml` 的 test job）的 `actions/checkout`
        默认 `fetch-depth: 1` = 浅克隆、只有最新一个 commit，历史 sha 一律解析不出 ——
        那时本用例会对每条 `code_sha` 报「不是 commit」，而 manifest 数据其实是对的
        （2026-09-12 实测：当时 11 条非哨兵 `code_sha` 解出 6 个不同 commit，
        在完整克隆里全部 resolve 成 commit —— 是浅克隆的锅，不是数据错）。

        故该 job 显式 `fetch-depth: 0`（那是**承重**配置，不是性能调优），
        checkout 步旁有注释点明。**不要为了加速 CI 把它改回浅克隆**，也不要降级成
        「浅克隆下 skip 这条断言」—— 那等于在 CI 里关掉这条锁，而 CI 正是最该守它的
        地方（本地可能忘了跑）。降级即白建。
        """
        bad = []
        for e in _manifest():
            sha = (e.get("code_sha") or "").strip()
            if sha in _SHA_SENTINELS:
                continue
            r = subprocess.run(["git", "cat-file", "-t", sha], cwd=ROOT, capture_output=True)
            if r.returncode or r.stdout.decode("utf-8", "replace").strip() != "commit":
                bad.append((e["id"], sha))
        assert not bad, (
            f"code_sha 不是 commit 也不是 {_SHA_SENTINELS}：{bad}。"
            "编一个近似 sha 比留哨兵坏得多 —— 它会让复算的人 checkout 一个错的点。")

    def test_non_repo_paths_are_declared_both_ways(self):
        """notes / claim 里的非仓库路径必须**逐条**声明进 `non_repo_paths`，且只能声明非仓库的。

        此前是「整条 blob 里找『未入库』等词」（`any(m in blob)`）—— 一条目两条路径只标一条
        也过,标注与它管的那条路径之间没有绑定。改成声明式字段后**双向**核对：

        1. 解析不到仓库的 token 必须逐个出现在 `non_repo_paths`
        2. `non_repo_paths` 里的每一条必须**确实**解析不到仓库

        只查方向 1，作者可以把整个仓库路径表倒进来一劳永逸；只查方向 2，漏标的路径照旧无人
        发现。双向才让这个字段等价于一次**逐路径的显式表态**。前缀白名单（`_GITIGNORED_PREFIXES`）
        随之删除 —— 有了逐条声明，前缀猜测就是多余的，而且它本身就是启发式。
        """
        bad = []
        for e in _manifest():
            declared = e.get("non_repo_paths") or []
            blob = " ".join(str(e.get(f) or "") for f in ("claim", "notes"))
            undeclared = sorted(t for t in _path_tokens(blob)
                                if not _is_tracked(t) and t not in declared)
            if undeclared:
                bad.append((e["id"], "未声明", undeclared))
            falsely = sorted(t for t in declared if _is_tracked(t))
            if falsely:
                bad.append((e["id"], "谎称不在库", falsely))
        assert not bad, (
            f"`non_repo_paths` 双向核对失败：{bad}。"
            "方向一：claim/notes 里解析不到仓库的路径必须逐条声明；"
            "方向二：声明进来的路径必须真的不在库（tracked 的写进来即红）。")



class TestClaimBindings:
    """L3 —— `claim` 的数字必须能从入库产物按声明路径取出并相等（缺陷 14 v3 §五）。

    这是「简历数字」与「入库产物」之间**唯一**的连接点。在此之前 `claim` 是自由文本：
    清单可以声称任何数字，直接渲染进 `resume-numbers.md`。口径混用那次（合并中位数 vs
    分档中位数，算出 6487 / 1205 而正确值是 8191 / 1245）就是这么漏过去的，靠人工发现。

    止步于「脚本在库、数字与产物相等」：`producer` 跑出来是不是**真**这份产物，静态不可判。
    """

    def _artifact(self, e: dict) -> dict:
        return json.loads((ROOT / e["artifact"]).read_text(encoding="utf-8"))

    def test_assertions_present_for_verified_and_null_otherwise(self):
        bad = []
        for e in _manifest():
            a = e.get("assertions", "缺失")
            if e.get("status") == "verified":
                if not isinstance(a, list) or not a:
                    bad.append((e["id"], a))
            elif a is not None:
                bad.append((e["id"], a))
        assert not bad, (
            f"verified 必须有非空 assertions、其余两档必须 null：{bad}。"
            "空列表等于零绑定 —— claim 又变回自由文本。")

    def test_assertion_values_match_artifact(self):
        bad = []
        for e in _manifest():
            if e.get("status") != "verified":
                continue
            art = self._artifact(e)
            for a in e["assertions"]:
                try:
                    got = _resolve(art, a["path"])
                except Exception as ex:  # 键不存在 / 下标越界 / 类型不对
                    bad.append((e["id"], a["path"], f"解析失败：{type(ex).__name__}"))
                    continue
                if "len" in a:
                    ok = hasattr(got, "__len__") and len(got) == a["len"]
                    want = a["len"]
                else:
                    want = a["value"]
                    # 严格相等：`True == 1` 在 Python 里成立，不比对类型会漏掉真变异
                    ok = type(got) is type(want) and got == want
                if not ok:
                    bad.append((e["id"], a["path"], f"产物 {got!r} ≠ 声明 {want!r}"))
        assert not bad, (
            f"assertion 与产物对不上（id, path, 差异）：{bad}。"
            "改数据前先确认是产物变了还是声明写错了 —— 不得用重跑顶替旧结论。")

    def test_claim_numbers_are_bound_or_declared_derived(self):
        bad = []
        for e in _manifest():
            if e.get("status") != "verified":
                continue
            bound = {v for a in e["assertions"] for v in (a.get("value"), a.get("len"))
                     if isinstance(v, int) and not isinstance(v, bool)}
            derived = {d.get("value") for d in (e.get("derived") or [])
                       if isinstance(d, dict) and isinstance(d.get("value"), int)
                       and not isinstance(d.get("value"), bool)}
            missing = sorted(_numbers(e.get("claim") or "") - bound - derived)
            if missing:
                bad.append((e["id"], f"claim 数字无出处：{missing}"))
        assert not bad, (
            f"claim 的数字必须被某条 assertion 覆盖、或写进 `derived`（附 refs 与 formula）：{bad}。"
            "既绑不上产物、又说不出算法的数字，该做的是从 claim 里删掉它 —— 不是造一个 derived 糊过去。")

    def test_derived_present_for_verified_and_null_otherwise(self):
        """`derived` 与 `assertions` 同规则：verified 必填（可为空列表），其余两档 null。"""
        bad = []
        for e in _manifest():
            d = e.get("derived", "缺失")
            if e.get("status") == "verified":
                if not isinstance(d, list):
                    bad.append((e["id"], d))
            elif d is not None:
                bad.append((e["id"], d))
        assert not bad, (
            f"verified 必须有 derived（无派生量时写空列表）、其余两档必须 null：{bad}。")

    def test_derived_values_come_from_bound_refs(self):
        """派生量只能从**已绑定**的量派生 —— 三条同时成立才算绑定。

        1. `refs` 非空，且逐个落在本条目 `assertions` 的 `path` 集合里
        2. `value` 不等于任何单个 `ref` 解析出的值（恒等式即红）
        3. `formula` 非空

        为什么这样能拦住旧写法：`派生量：9999=9999` 那种在 notes 里自说自话的形态，改写成
        结构化字段后**过不去** —— `9999` 没有可引的 assertion path。要让一个数字合法，作者
        必须先把它依赖的量绑到产物上，逃逸的成本变成了「先把真话说出来」。
        """
        bad = []
        for e in _manifest():
            if e.get("status") != "verified":
                continue
            paths = {a["path"] for a in (e.get("assertions") or [])}
            art = self._artifact(e)
            for d in (e.get("derived") or []):
                if not isinstance(d, dict) or "value" not in d:
                    bad.append((e["id"], repr(d), "条目缺 value"))
                    continue
                refs = d.get("refs") or []
                if not refs:
                    bad.append((e["id"], d["value"], "refs 为空 —— 派生量没有来源"))
                    continue
                outside = sorted(r for r in refs if r not in paths)
                if outside:
                    bad.append((e["id"], d["value"], f"refs 不在本条目 assertions 里：{outside}"))
                    continue
                if not str(d.get("formula") or "").strip():
                    bad.append((e["id"], d["value"], "formula 为空 —— 只有数字不叫算法"))
                    continue
                for r in refs:
                    try:
                        got = _resolve(art, r)
                    except Exception as ex:
                        bad.append((e["id"], d["value"], f"ref {r} 解析失败：{type(ex).__name__}"))
                        break
                    if type(got) is type(d["value"]) and got == d["value"]:
                        bad.append((e["id"], d["value"], f"恒等式：ref {r} 本身就等于它"))
                        break
        assert not bad, (
            f"派生量必须能从已绑定的量算出（id, value, 问题）：{bad}。"
            "注意比类型：`0 == False` 在 Python 里成立，不比对类型会漏掉真变异。")


class TestWriterContract:
    """出口行为 —— 闸 1 的变异靶。落点由 fixture 指到 tmp，边界条件与真实目录无关。"""

    def _write(self, payload, **over):
        kwargs = dict(claim="c", script="tests/perf/probe.py", env="e", code_sha="deadbee")
        kwargs.update(over)
        return evidence_writer.write_evidence("tmp-id", payload, **kwargs)

    @staticmethod
    def _entry(ev: Path) -> dict:
        return json.loads((ev / "manifest.json").read_text(encoding="utf-8"))[0]

    @staticmethod
    def _body(ev: Path) -> dict:
        return json.loads((ev / "tmp-id.json").read_text(encoding="utf-8"))

    def test_landing_zone_is_evidence_dir_and_id_named(self, tmp_evidence):
        artifact = self._write({"a": 1, "b": 2})
        assert artifact == tmp_evidence / "tmp-id.json"
        assert self._body(tmp_evidence) == {"a": 1, "b": 2}

    def test_manifest_entry_is_verified_and_complete(self, tmp_evidence):
        self._write({"a": 1})
        e = self._entry(tmp_evidence)
        assert e["status"] == "verified"
        assert e["artifact"] == "docs/evidence/tmp-id.json"
        assert e["script"] == "tests/perf/probe.py"
        assert e["reproduce"] == "PROBE_EVIDENCE_ID=tmp-id python tests/perf/probe.py"
        assert e["env"] == "e" and e["code_sha"] == "deadbee"
        assert e["measured_at"] == date.today().isoformat()
        assert e["assertions"] == [] and e["derived"] == []
        assert e["redacted_fields"] == []

    def test_whitelist_drops_and_accounts_unlisted_keys(self, tmp_evidence):
        self._write({"a": 1, "b": 2, "preview": "正文", "content_head": "正文"})
        assert self._body(tmp_evidence) == {"a": 1, "b": 2}
        assert self._entry(tmp_evidence)["redacted_fields"] == ["content_head", "preview"]

    def test_upsert_replaces_same_id_instead_of_appending(self, tmp_evidence):
        self._write({"a": 1})
        self._write({"a": 2}, claim="c2")
        entries = json.loads((tmp_evidence / "manifest.json").read_text(encoding="utf-8"))
        assert len(entries) == 1 and entries[0]["claim"] == "c2"

    def test_subset_may_narrow(self, tmp_evidence):
        self._write({"a": 1, "b": 2}, subset={"a"})
        assert self._body(tmp_evidence) == {"a": 1}
        assert self._entry(tmp_evidence)["redacted_fields"] == ["b"]

    def test_subset_may_not_widen(self, tmp_evidence):
        with pytest.raises(ValueError, match="越界"):
            self._write({"a": 1}, subset={"a", "nope"})

    def test_unregistered_id_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="未在 evidence_writer"):
            evidence_writer.write_evidence(
                "never-registered", {"a": 1},
                claim="c", script="s", env="e", code_sha="d")

    def test_empty_whitelist_refused(self, tmp_evidence, monkeypatch):
        monkeypatch.setitem(evidence_writer._ALLOWED_BY_ID, "empty-id", frozenset())
        with pytest.raises(ValueError, match="白名单为空"):
            evidence_writer.write_evidence(
                "empty-id", {"a": 1}, claim="c", script="s", env="e", code_sha="d")

    @pytest.mark.parametrize("field", ["claim", "script", "env", "code_sha"])
    def test_blank_manifest_fields_refused(self, tmp_evidence, field):
        with pytest.raises(ValueError, match=repr(field)):
            self._write({"a": 1}, **{field: "   "})

    def test_non_dict_payload_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="必须是 dict"):
            self._write([1, 2, 3])

    def test_writer_exposes_no_path_or_whitelist_override(self):
        """落点与白名单不能由调用方改 —— 留口子就等于留回退路径。"""
        forbidden = {"path", "out", "out_dir", "out_path", "evidence_dir", "dir",
                     "allowed", "allowed_keys", "keys", "relax", "force"}
        for fn in (evidence_writer.write_evidence, evidence_writer.register_artifact):
            params = set(inspect.signature(fn).parameters)
            assert not (params & forbidden), \
                f"{fn.__name__} 出现了可覆盖的口子：{sorted(params & forbidden)}"

    def test_landing_zone_is_not_env_overridable(self):
        src = (PERF / "evidence_writer.py").read_text(encoding="utf-8")
        assert "os.environ" not in src and "getenv" not in src, (
            "落点不得读环境变量 —— PROBE_OUT_DIR 的默认值就是这么变成 gitignored 的。")


class TestRegisterArtifact:
    """迁移侧入口 —— 产物已在盘上，只补登记（不重跑顶替）。"""

    def test_registers_existing_artifact_with_historical_date(self, tmp_evidence):
        (tmp_evidence / "tmp-id.json").write_text("{}\n", encoding="utf-8")
        evidence_writer.register_artifact(
            "tmp-id", claim="c", script="tests/perf/map_len_probe.py", env="e",
            code_sha="abc1234", measured_at="2026-09-10", script_role="producer", notes="n",
            assertions=[{"path": "a", "value": 1}], derived=[], non_repo_paths=[],
            redacted_fields=("content_head", "preview"))
        e = json.loads((tmp_evidence / "manifest.json").read_text(encoding="utf-8"))[0]
        assert e["status"] == "verified"
        assert e["measured_at"] == "2026-09-10"
        assert e["script_role"] == "producer"
        assert e["artifact"] == "docs/evidence/tmp-id.json"
        assert e["assertions"] == [{"path": "a", "value": 1}]
        assert e["derived"] == []
        assert e["non_repo_paths"] == []
        assert e["redacted_fields"] == ["content_head", "preview"]

    def test_missing_artifact_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="不存在"):
            evidence_writer.register_artifact(
                "tmp-id", claim="c", script="s", env="e", script_role="producer",
                assertions=[], derived=[], non_repo_paths=[],
                code_sha="a", measured_at="2026-09-12")

    def test_register_requires_declarations_without_defaults(self, tmp_evidence):
        """迁移入口的 `script_role` / `assertions` / `derived` / `non_repo_paths` 无默认值 —— 漏填 TypeError。"""
        (tmp_evidence / "tmp-id.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(TypeError):
            evidence_writer.register_artifact(
                "tmp-id", claim="c", script="s", env="e",
                code_sha="a", measured_at="2026-09-12")

    def test_bad_script_role_refused(self, tmp_evidence):
        (tmp_evidence / "tmp-id.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="script_role 只允许"):
            evidence_writer.register_artifact(
                "tmp-id", claim="c", script="s", env="e", script_role="secondary",
                assertions=[], derived=[], non_repo_paths=[],
                code_sha="a", measured_at="2026-09-12")

    def test_unregistered_id_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="未在 evidence_writer"):
            evidence_writer.register_artifact(
                "never-registered", claim="c", script="s", env="e", script_role="producer",
                assertions=[], derived=[], non_repo_paths=[],
                code_sha="a", measured_at="2026-09-12")


class TestRenderedResumeList:
    """渲染产物不得与清单脱钩 —— 改了清单而没重跑脚本，这里红。

    `docs/evidence/resume-numbers.md` 是给人看的「哪些数字能写进简历」，内容全部来自清单。
    没有这道锁，它一周内就会变成第三份手工表（正是缺陷 14 要消灭的形态）。
    """

    def test_resume_numbers_is_current(self):
        import render_evidence
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert entries, "清单为空 —— 这条断言会空过，先查为什么没有条目"
        assert render_evidence.OUT.read_text(encoding="utf-8") == render_evidence.render(entries), (
            "docs/evidence/resume-numbers.md 与清单不一致 —— 重跑 "
            "`python tests/perf/render_evidence.py`（本文件是渲染产物，勿手改）")


@pytest.fixture
def tmp_evidence(tmp_path, monkeypatch):
    """把出口的 ROOT / 落点整体搬到 tmp —— 断言里的 docs/evidence/ 前缀因此仍然成立。"""
    monkeypatch.setattr(evidence_writer, "ROOT", tmp_path)
    monkeypatch.setattr(evidence_writer, "EVIDENCE_DIR", tmp_path / "docs" / "evidence")
    monkeypatch.setattr(evidence_writer, "MANIFEST", tmp_path / "docs" / "evidence" / "manifest.json")
    monkeypatch.setitem(evidence_writer._ALLOWED_BY_ID, "tmp-id", frozenset({"a", "b"}))
    ev = tmp_path / "docs" / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    return ev
