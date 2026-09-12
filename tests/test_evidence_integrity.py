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
    "verified": ("artifact", "script", "script_role", "reproduce"),
    "runtime-measured": ("env", "measured_at", "notes"),
    "unverifiable": ("measured_at", "env", "notes"),
}
_STATUSES = ("verified", "runtime-measured", "unverifiable")
_SCRIPT_ROLES = ("producer", "corroborating")

# ── L2 指称闭合（缺陷 14 v3 §四）：字段值必须解析回仓库里的实体，不能只校验形状 ──
# `producer` 的定义是「跑它能重生成这份产物」，脚本不在库里这个定义当场不成立 ——
# 而在此之前没有任何断言看 `script` 一眼。
_SHA_SENTINELS = ("unknown(scratch)",)
# 正文里允许出现的「非仓库」路径：这些前缀下的文件本来就不入库，但必须**在同一条目内**
# 带一句非仓库标注，读者才知道按它去查是查不到的。
_GITIGNORED_PREFIXES = (".claude/sessions/", "e2e/scratch/", "graphify-out/", "config.yaml")
# reproduce 允许引用的运行期输入（语料库等）—— 复现命令读它没问题，只是它不入库
_RUNTIME_OK_PREFIXES = ("data/",) + _GITIGNORED_PREFIXES
_NONREPO_MARKERS = ("未入库", "不在仓库", "gitignored", "scratch", ".gitignore")
_PATH_RE = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)*[\w.-]+\.(?:py|json|md|sh|txt|ya?ml|cjs|js|ts|tsx|sql|toml|ini|cfg)")

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

    def test_notes_and_claim_paths_resolve(self):
        """notes / claim 里的路径要么 tracked，要么带非仓库标注。

        迁移与改名是这类漂移的高发场景：文档里那条路径早已不在，读者按它去查是空的，
        而清单本身看起来完好。
        """
        bad = []
        for e in _manifest():
            blob = " ".join(str(e.get(f) or "") for f in ("claim", "notes"))
            for tok in _path_tokens(blob):
                if _is_tracked(tok):
                    continue
                if tok.startswith(_GITIGNORED_PREFIXES):
                    if not any(m in blob for m in _NONREPO_MARKERS):
                        bad.append((e["id"], tok, "gitignored 前缀但全条无「非仓库」标注"))
                    continue
                bad.append((e["id"], tok, "不在库"))
        assert not bad, (
            f"notes/claim 里的路径解析不到：{bad}。"
            f"gitignored 前缀 {_GITIGNORED_PREFIXES} 必须带非仓库标注（如「未入库」）。")



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
            redacted_fields=("content_head", "preview"))
        e = json.loads((tmp_evidence / "manifest.json").read_text(encoding="utf-8"))[0]
        assert e["status"] == "verified"
        assert e["measured_at"] == "2026-09-10"
        assert e["script_role"] == "producer"
        assert e["artifact"] == "docs/evidence/tmp-id.json"
        assert e["redacted_fields"] == ["content_head", "preview"]

    def test_missing_artifact_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="不存在"):
            evidence_writer.register_artifact(
                "tmp-id", claim="c", script="s", env="e", script_role="producer",
                code_sha="a", measured_at="2026-09-12")

    def test_register_requires_script_role(self, tmp_evidence):
        """迁移入口的 `script_role` 无默认值 —— 漏填给 TypeError，不给静默兜底。"""
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
                code_sha="a", measured_at="2026-09-12")

    def test_unregistered_id_refused(self, tmp_evidence):
        with pytest.raises(ValueError, match="未在 evidence_writer"):
            evidence_writer.register_artifact(
                "never-registered", claim="c", script="s", env="e", script_role="producer",
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
