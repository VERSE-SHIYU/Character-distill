"""证据产物的**唯一写入出口** —— 落点固定、写时脱敏、同时登记清单。

为什么存在（缺陷 14）：在此之前「探针产物」不是仓里的一等类别 —— 探针各自选落点
（``PROBE_OUT_DIR`` / ``PROBE_OUT`` / stdout 重定向），默认值一律指向 gitignored 目录，
于是凡被正文引用的数字都要临时开一次例外；脱敏靠记性（``AGENTS.md`` 记着「入库产物已删
``preview`` / ``content_head``」，一次手工动作，没有任何东西保证下一批也删）。

本模块把三件事变成结构上的必然：

1. **落点只有一个** —— ``docs/evidence/<id>.json``，**不提供路径参数、不读环境变量**。
   留口子就等于留回退路径，``PROBE_OUT_DIR`` 的默认值就是这么变成 gitignored 的
2. **脱敏在写入时按白名单执行** —— 黑名单只挡已知字段名，探针加一个新字段就漏
3. **清单在写入时登记** —— ``manifest.json`` 是唯一真源，正文按 ``id`` 引用

探针唯一需要知道的接口::

    from evidence_writer import code_sha, write_evidence

    write_evidence(
        "thinking-maplen-after",
        payload,
        claim="out_tokens p50 8191 → 1245（n=14，同语料同提示词前后对照）",
        script="tests/perf/map_len_probe.py",
        env="deepseek-v4-pro，单供应商，temperature=0.7，attempt/deadline 抬到 120/240",
        code_sha=code_sha(),
    )

探针选哪个 id 由 ``PROBE_EVIDENCE_ID`` 传入（见 ``reproduce`` 字段的默认值）。
契约细节（三档 status、白名单上限、引用语法）见 ``docs/evidence/README.md``。
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "docs" / "evidence"
MANIFEST = EVIDENCE_DIR / "manifest.json"

STATUSES = ("verified", "runtime-measured", "unverifiable")

# script 字段的两种语义 —— 不加这个字段，区别只活在散文里，锁看不见，
# 下一个人照抄「verified + script 指向别的文件」的形态就会填出一条真不可复现的条目。
#   producer      —— 跑它**重生成**这份产物
#   corroborating —— 只覆盖同一断言（例如产物来自未入库的一次性 scratch 脚本）
SCRIPT_ROLES = ("producer", "corroborating")

# id → 允许落盘的顶层键。**新探针接入时必须在此注册**，未注册直接抛错：
# 没注册就没有白名单，脱敏又回到「记得删」。锁测试扫正文的 ev: 引用，未注册即红。
_ALLOWED_BY_ID: dict[str, frozenset[str]] = {
    # map_len_probe —— 修复 thinking 方言前后各一批（n=14，同语料同提示词）
    "thinking-maplen-after": frozenset(
        {"probe", "model", "measure_max_tokens", "prod_max_tokens", "records", "summary"}),
    "thinking-maplen-before": frozenset(
        {"probe", "model", "measure_max_tokens", "prod_max_tokens", "records", "summary"}),
    # capfield_probe —— 顶到 8192 上限时 token 花在哪
    "thinking-capfield": frozenset({"probe", "model", "cap", "records"}),
    # raise_probe（pytest 插件）—— 每条属主用例实际命中的 raise 站点
    "ownership-reachability": frozenset({"no_key_mode", "records"}),
    "ownership-reachability-nokey": frozenset({"no_key_mode", "records"}),
    # 断点行删除路径残留 + 续跑可达性（双 store，确定性，无 LLM）
    "distill-orphan-matrix": frozenset({"probe", "stores"}),
    "distill-resume-reachability": frozenset({"probe", "stores"}),
    # leak_probe —— rig 上单真实请求的线程残留窗（需 mock + 容器，见 tests/perf/README.md）
    "tools-leak-window": frozenset(
        {"probe", "embed_hang_ms", "baseline_threads", "peak_threads", "thread_delta",
         "requests_done_s", "leak_window_s", "settle_s", "samples"}),
    # e2e/scratch/resume_probe.py 的 v5 迁移快照（断点续跑：52 字节落满 6 片、二次续跑 map=0）。
    # 只收统计量：sampleChunk 是 52 字节被截断的**正文段**，finalMessage 是装饰文案，
    # taskId 是运行期句柄 —— 三者都没有证据价值，留在白名单外才是结构上的保证。
    "incomplete-v5": frozenset(
        {"probe", "item", "totalChunks", "startStatus", "finalStatus", "mockTruncateEnabled",
         "stats", "chunksInDb", "chunkLengths", "allNonEmpty", "allEqualTruncated",
         "chunkParsesAsJson", "resumeStartStatus", "resumeFinalStatus", "resumeStats",
         "resumeMapCalls", "truncatedReusedByGate2", "ok"}),
    # ── 以下三条**没有产物**（status = runtime-measured / unverifiable）：空白名单是它们的
    # 正确登记形态 —— 注册一个空集，结构上就堵死了「哪天有人往里写产物」这条路。
    # write_evidence 只在**调用时**拒绝空集（见其 docstring），注册时不拦，正是为此。
    "a2-wiring-mutation": frozenset(),
    "chunk-size-provenance": frozenset(),
    "config-yaml-values": frozenset(),
    "graphify-snapshot-2026-08-15": frozenset(),
}


def code_sha() -> str:
    """产出时的 commit —— 清单字段，调用方传进来（本函数只是免去 7 处重复抄 git 命令）。"""
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT, capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8", "replace").strip()


def write_evidence(
    evidence_id: str,
    payload: dict,
    *,
    claim: str,
    script: str,
    env: str,
    code_sha: str,
    subset: frozenset[str] | set[str] | None = None,
    reproduce: str | None = None,
    script_role: str = "producer",
    assertions: list[dict] | None = None,
    extra_notes: str | None = None,
) -> Path:
    """写 ``docs/evidence/<id>.json`` 并 upsert 清单条目（``status = verified``）。

    拒绝（抛 ``ValueError``，不落文件）：id 未注册 / 白名单为空 / ``subset`` 越界 /
    ``claim`` ``script`` ``env`` ``code_sha`` 任一缺失或空白 / ``script_role`` 非法 /
    payload 不是 dict。探针产产物，故 ``script_role`` 默认 ``producer``。

    ``assertions``（``claim`` 里每个数字的产物出处，见 ``docs/evidence/README.md``）默认
    为空 —— 探针不传时**锁会红**：实测产物刚跑出来，哪些数字是它的主张，只有作者知道。
    """
    allowed = _ALLOWED_BY_ID.get(evidence_id)
    if allowed is None:
        raise ValueError(
            f"证据 id {evidence_id!r} 未在 evidence_writer._ALLOWED_BY_ID 注册。"
            "先注册它的白名单（允许落盘的顶层键），否则脱敏无从谈起。")
    if not allowed:
        raise ValueError(f"证据 id {evidence_id!r} 的白名单为空 —— 空集等于什么都没保证。")

    keep = frozenset(allowed if subset is None else subset)
    if not keep:
        raise ValueError("subset 为空 —— 收窄可以，收成空集不行。")
    out_of_bounds = sorted(keep - allowed)
    if out_of_bounds:
        raise ValueError(
            f"subset 越界：{out_of_bounds} 不在 {evidence_id!r} 的白名单里。"
            "探针只能收窄，不能放宽。")

    for name, value in (("claim", claim), ("script", script), ("env", env), ("code_sha", code_sha)):
        if not (isinstance(value, str) and value.strip()):
            raise ValueError(f"清单字段 {name!r} 缺失或为空白 —— 缺它这条就不可追溯。")
    if not isinstance(payload, dict):
        raise ValueError(f"payload 必须是 dict，收到 {type(payload).__name__}。")
    _check_script_role(script_role)

    # 按 payload 原顺序保留允许的键；白名单外的键丢弃并记账
    body = {k: v for k, v in payload.items() if k in keep}
    redacted = sorted(set(payload) - keep)

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    artifact = EVIDENCE_DIR / f"{evidence_id}.json"
    artifact.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _upsert({
        "id": evidence_id,
        "claim": claim.strip(),
        "status": "verified",
        "artifact": artifact.relative_to(ROOT).as_posix(),
        "script": script,
        "script_role": script_role,
        "reproduce": reproduce or f"PROBE_EVIDENCE_ID={evidence_id} python {script}",
        "assertions": list(assertions or []),
        "env": env.strip(),
        "measured_at": date.today().isoformat(),
        "code_sha": code_sha.strip(),
        "redacted_fields": redacted,
        "notes": (extra_notes or "").strip(),
    })
    return artifact


def register_artifact(
    evidence_id: str,
    *,
    claim: str,
    script: str,
    env: str,
    code_sha: str,
    measured_at: str,
    script_role: str,
    assertions: list[dict],
    reproduce: str | None = None,
    redacted_fields: tuple[str, ...] | list[str] = (),
    notes: str | None = None,
) -> Path:
    """登记一份**已经在盘上**的产物（迁移冻结快照用，探针不要调这个）。

    ``write_evidence`` 是「跑探针 → 产生产物」；本函数是「产物本来就在 → 补登记」。
    迁移历史产物时不能重跑顶替（重跑得到的是今天的数字，正文写的是当时的结论），
    所以落点、白名单、字段校验仍与 ``write_evidence`` 同一套，只是不写 payload。

    ``script_role`` 与 ``assertions`` **无默认值、必须表态** —— 迁移路径正是最容易
    静默填错的那条（先例：``incomplete-v5`` 的 ``script`` 指向的不是产出脚本）。给默认值
    就等于给「照抄时留空」留口子：``assertions`` 是 ``claim`` 与产物之间唯一的连接点，
    空着它 ``claim`` 就又变回自由文本。
    """
    if evidence_id not in _ALLOWED_BY_ID:
        raise ValueError(
            f"证据 id {evidence_id!r} 未在 evidence_writer._ALLOWED_BY_ID 注册。")
    for name, value in (("claim", claim), ("script", script), ("env", env),
                        ("code_sha", code_sha), ("measured_at", measured_at)):
        if not (isinstance(value, str) and value.strip()):
            raise ValueError(f"清单字段 {name!r} 缺失或为空白 —— 缺它这条就不可追溯。")
    _check_script_role(script_role)

    artifact = EVIDENCE_DIR / f"{evidence_id}.json"
    if not artifact.exists():
        raise ValueError(
            f"{artifact.relative_to(ROOT).as_posix()} 不存在 —— 迁移用本函数，"
            "新产物请跑探针走 write_evidence。")

    _upsert({
        "id": evidence_id,
        "claim": claim.strip(),
        "status": "verified",
        "artifact": artifact.relative_to(ROOT).as_posix(),
        "script": script,
        "script_role": script_role,
        "reproduce": reproduce or f"PROBE_EVIDENCE_ID={evidence_id} python {script}",
        "assertions": list(assertions),
        "env": env.strip(),
        "measured_at": measured_at.strip(),
        "code_sha": code_sha.strip(),
        "redacted_fields": sorted(redacted_fields),
        "notes": (notes or "").strip(),
    })
    return artifact


def _check_script_role(script_role: str) -> None:
    if script_role not in SCRIPT_ROLES:
        raise ValueError(
            f"script_role 只允许 {SCRIPT_ROLES}，收到 {script_role!r}。"
            "对已入库产物用 producer；script 指向的文件不产这份产物就用 corroborating"
            "（且必须在 notes 里说明产出脚本是谁、为什么没入库）。")


def _upsert(entry: dict) -> None:
    """清单按 id 替换（不追加第二份），按 id 排序落盘，便于 review diff。"""
    entries: list[dict] = []
    if MANIFEST.exists() and MANIFEST.read_text(encoding="utf-8").strip():
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [e for e in entries if e.get("id") != entry["id"]]
    entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    MANIFEST.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
