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

# id → 允许落盘的顶层键。**新探针接入时必须在此注册**，未注册直接抛错：
# 没注册就没有白名单，脱敏又回到「记得删」。锁测试扫正文的 ev: 引用，未注册即红。
_ALLOWED_BY_ID: dict[str, frozenset[str]] = {}


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
    extra_notes: str | None = None,
) -> Path:
    """写 ``docs/evidence/<id>.json`` 并 upsert 清单条目（``status = verified``）。

    拒绝（抛 ``ValueError``，不落文件）：id 未注册 / 白名单为空 / ``subset`` 越界 /
    ``claim`` ``script`` ``env`` ``code_sha`` 任一缺失或空白 / payload 不是 dict。
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
        "reproduce": reproduce or f"PROBE_EVIDENCE_ID={evidence_id} python {script}",
        "env": env.strip(),
        "measured_at": date.today().isoformat(),
        "code_sha": code_sha.strip(),
        "redacted_fields": redacted,
        "notes": (extra_notes or "").strip(),
    })
    return artifact


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
