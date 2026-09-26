# -*- coding: utf-8 -*-
"""红楼梦级长书蒸馏验收（蓝图 §10 A–D）—— 一条命令跑一种模式，按 D3 模板打印读数。

为什么入 git：§11.3 要求交付报告照填 §10 D3 的读数模板；产数逻辑留在 scratch 里，
三个月后没人复现得出那组数字（`tests/perf/README.md` 记的就是这条教训）。

两种模式（`--mode`），共用同一套环境核对与日志计数：
  identify —— §10 A（环境对齐）+ B（识别质量与耗时）
  distill  —— §10 A + C1–C4（起任务/轮询、分阶段耗时、卡片校验与原文命中）
两者都打印 D1 日志计数，并按 D3 模板输出。

刻意不做的事：
  - **不落盘卡片与原文**：卡片只在内存里校验，命中判据用子串查找；原文只读进内存，
    不写任何文件（版权语料只留统计量；§10 C4 明写「卡片不入库、不进 commit」）。
  - **不打印任何凭据**：DSN / 口令 / API key 只从环境变量读；出错也不回显其值。
  - **不调真 API 也能跑**：脚本只经 HTTP 打本地栈，LLM 指向谁由那个栈自己的配置决定。
    用 `tests/perf/mock_llm_server.py` 起假服务、把栈的 llm.base_url 指过去即可空跑。

用法::

    export ACCEPTANCE_PASSWORD=...        # 必填（testadmin 的口令），不走命令行
    export DATABASE_URL=postgresql://...  # 读 usage_stats 与原文，与 app 同源
    python tests/perf/longbook_acceptance.py --mode identify --text-id <id> \\
        --criteria criteria.json [--app-log app.log]
    python tests/perf/longbook_acceptance.py --mode distill --text-id <id> \\
        --character <名> [--app-log app.log]

`criteria.json`（identify 必填）。判据跟着语料走，故**不入库**，由调用方给::

    {
      "main":           ["角色1", ...],        # 必须恰 1 组命中、且该组 importance == 主要
      "same_group":     [["角色1", "别名A"]],  # 两个称呼必须落进同一组
      "generics":       ["泛称1", ...],        # 不得是任何组的 name
      "generic_not_on": ["角色1", ...]         # 泛称也不得出现在这些人的 aliases 里
    }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_writer import code_sha, write_evidence  # noqa: E402
from core.distiller import Distiller  # noqa: E402
from core.schema import CharacterCard  # noqa: E402

# ── §10 的硬数字与门槛（照抄蓝图，不在这里另立一套） ──────────────────────
EXPECTED_CHARS = 866_149       # §10 A3：不是这份就不是同一本书
EXPECTED_IDENTIFY_CHUNKS = 242  # §10 A3：_split_chunks(text, 5000) 的片数
EXPECTED_CHUNK_SIZE = 5000     # §10 A1：_chunk_size 必须 5000
EXPECTED_MAP_CONCURRENCY = 60  # §10 A1：WP6 实测更正后的值（原 250 撞 429）
EXPECTED_MODEL = "deepseek-v4-pro"   # §10 A1
IDENTIFY_CHUNK_SIZE = 5000     # 识别用 self._chunk_size，不看 text_type（§1.28）
IDENTIFY_LIMIT_S = 480.0       # §10 B4 / D2：整请求 ≤ 8 分钟
DISTILL_LIMIT_S = 300.0        # §10 D2：宝玉 > 5 分钟即停
CARD_MAX_TOKENS = 8192         # §10 D2：任一批 completion_tokens ≥ 它即停（截断）
POLL_S = 1.0                   # §10 C1：每 1 s 轮询
HITS = 5                       # §10 C4：对话示例 / 口癖各取前 5 条做原文命中

# ── D1：服务端日志计数 ────────────────────────────────────────────────
# 5xx 只认访问日志里状态码的位置，不数正文里的「5xx」字样 —— 后者会把
# 「文档里提到 5xx」也算进去，读数就不可信了。
LOG_PATTERNS = (
    ("429", r"\b429\b"),
    ("5xx", r'"\s*5\d\d\s'),
    ("读取流式响应失败", r"读取流式响应失败"),
    ("Reduce batch", r"Reduce batch"),
    # §10 D2「任一分片最终失败」：`Map chunk %s failed` 是 Map 里某片重试墙后仍未成的
    # 唯一落点（`core/distiller.py` 的 `_run_map_concurrent._one`），全仓只此一处。
    ("分片失败", r"Map chunk \d+ failed"),
)


def eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


# ══ 基础设施 ══════════════════════════════════════════════════════════

def http_client(base_url: str, token: str = ""):
    """带鉴权的 HTTP 客户端。read 超时覆盖整条识别请求（最长 8 分钟）。"""
    import httpx
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(
        base_url=base_url, headers=headers, follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=IDENTIFY_LIMIT_S + 120.0),
    )


def login(base_url: str, username: str, password: str) -> tuple[str, str]:
    """登录拿 (token, user_id)。口令只经参数传入，不回显、不落盘。"""
    with http_client(base_url) as c:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"登录失败：HTTP {r.status_code}")
    data = r.json()
    return data["access_token"], data["user"]["id"]


async def _fetch(dsn: str, sql: str, *args):
    import asyncpg
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def fetch(dsn: str, sql: str, *args) -> list:
    """同步取 DB 行。DSN 只在异常里报**变量名**，不报值。"""
    try:
        return asyncio.run(_fetch(dsn, sql, *args))
    except Exception as exc:
        eprint(f"[db] 查询失败（DATABASE_URL）：{type(exc).__name__}")
        return []


def count_log(path: str | None) -> dict[str, int | str]:
    """D1：数服务端日志里的 429 / 5xx / 读取流式响应失败 / Reduce batch / 分片失败。

    日志由调用方用 `--app-log` 给出（例如 `docker compose logs app > app.log`）；
    没给就如实报「未提供」，不假装是 0 —— 0 与「没数过」是两件事。
    """
    if not path:
        return {label: "未提供" for label, _ in LOG_PATTERNS}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        eprint(f"[log] 读日志失败：{type(exc).__name__}")
        return {label: "读不到" for label, _ in LOG_PATTERNS}
    return {label: len(re.findall(pat, text)) for label, pat in LOG_PATTERNS}


# ══ §10 A：环境对齐（两种模式共用） ═══════════════════════════════════

def env_check(dsn: str, text_id: str) -> dict:
    """A1–A3。任一不符只报读数、不擅自改环境（§10 A 要求「停下」由人判）。"""
    out: dict = {}

    # A1：生产无 config.yaml；本地有就要先改名，否则跑的不是生产配置
    cfg = ROOT / "config.yaml"
    out["config_yaml"] = "缺（生产形态）" if not cfg.exists() else "在（验收前应改名 config.yaml.bak）"

    # A1：三者取自 app 自己的构造路径，与「跑起来的那个栈」同源（栈从本 worktree 起）
    try:
        from adapters.llm_adapter import LLMAdapter
        llm = LLMAdapter()
        dist = Distiller(llm)
        out["chunk_size"] = dist._chunk_size
        out["map_concurrency"] = dist._map_concurrency
        out["model"] = llm.model
        try:
            # `_request_options()` 恒等于「关闭思考」的方言 payload：非空即关，空 = 该
            # 供应商无此开关（`_THINKING_DISABLED` 的 unknown 档）。报原始 payload 只在
            # 方言表增删时才读得懂，故这里归一到「关/无此开关」两态。
            out["thinking"] = "关" if llm._request_options() else "无此开关"
        except Exception:
            out["thinking"] = "读不到"
    except Exception as exc:
        eprint(f"[env] 构造 Distiller 失败：{type(exc).__name__}")
        out["chunk_size"] = out["map_concurrency"] = out["model"] = out["thinking"] = "失败"
    # A1 的判定：三项各自比期望，不合并成一个布尔（否则读不出是哪项不符）
    out["a1"] = [
        ("chunk_size", out["chunk_size"], EXPECTED_CHUNK_SIZE),
        ("map_concurrency", out["map_concurrency"], EXPECTED_MAP_CONCURRENCY),
        ("model", out["model"], EXPECTED_MODEL),
        ("thinking", out["thinking"], "关"),
    ]

    # A2：本地 LLM_* 与生产逐项一致。生产那侧的值由人 dump 到文件后传入，脚本只比
    # 变量名与值的 sha256 前 8 位 —— 因此能报 diff，又不会把密钥写进任何地方。
    out["llm_env"] = _llm_env_compare(os.environ.get("ACCEPTANCE_PROD_LLM_ENV"))

    # A3：是不是同一份书
    rows = fetch(dsn, "SELECT content, text_type FROM texts WHERE id = $1", text_id)
    if not rows:
        out["text"] = "取不到（text_id 或 DATABASE_URL 不对）"
        return out
    content = rows[0]["content"] or ""
    out["chars"] = len(content)
    out["text_type"] = rows[0]["text_type"] or "story"
    out["identify_chunks"] = len(Distiller._split_chunks(content, IDENTIFY_CHUNK_SIZE)) if content else 0
    out["_content"] = content     # 只留在内存，供 B3 / C4 用；不写任何文件
    return out


def _llm_env_compare(prod_path: str | None) -> str:
    """比本地与生产的 `LLM_*`：变量名集合 + 值的 sha256 前 8 位。绝不回显值。"""
    import hashlib
    local = {k: v for k, v in os.environ.items() if k.startswith("LLM_")}
    if not prod_path:
        return f"本地 {len(local)} 项；未提供生产 dump（ACCEPTANCE_PROD_LLM_ENV）故未比对"
    try:
        lines = Path(prod_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "生产 dump 读不到"
    prod = {}
    for line in lines:
        if "=" in line and line.startswith("LLM_"):
            k, _, v = line.partition("=")
            prod[k.strip()] = v.strip()
    only_local = sorted(set(local) - set(prod))
    only_prod = sorted(set(prod) - set(local))
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:8]
    diff = sorted(k for k in set(local) & set(prod) if h(local[k]) != h(prod[k]))
    if not (only_local or only_prod or diff):
        return f"逐项一致（{len(local)} 项）"
    return (f"不一致｜仅本地 {only_local}｜仅生产 {only_prod}｜值不同 {diff}"
            "（只比名与值的 sha256 前 8 位）")


# ══ §10 B：识别 ═══════════════════════════════════════════════════════

def identify_mode(args, dsn: str, token: str, user_id: str, env: dict) -> dict:
    stats: dict = {}
    started_wall = datetime.now(timezone.utc)

    # B1：计时整请求；分阶段耗时取自 usage_stats（HTTP 路径只有汇总账）
    t0 = time.monotonic()
    try:
        with http_client(args.base_url, token) as c:
            r = c.post("/api/distill/identify", json={"text_id": args.text_id})
        elapsed = time.monotonic() - t0
        if r.status_code != 200:
            stats["error"] = f"HTTP {r.status_code}"
            chars = []
        else:
            chars = (r.json() or {}).get("characters") or []
    except Exception as exc:
        elapsed = time.monotonic() - t0
        stats["error"] = f"{type(exc).__name__}"
        chars = []
    stats["elapsed_s"] = round(elapsed, 1)
    stats["chars"] = chars

    rows = fetch(
        dsn,
        "SELECT action, prompt_tokens, completion_tokens, created_at FROM usage_stats "
        "WHERE user_id = $1 AND created_at >= $2 ORDER BY created_at",
        user_id, started_wall,
    )
    ident = [r for r in rows if r["action"] == "distill_identify"]
    stats["usage_rows"] = len(ident)
    stats["map_s"] = round((ident[0]["created_at"] - started_wall).total_seconds(), 1) if ident else None
    stats["alias_s"] = round((ident[-1]["created_at"] - started_wall).total_seconds(), 1) if len(ident) > 1 else None

    # B2：质量判据全部用代码判，不肉眼
    crit = _load_criteria(args.criteria)
    stats["criteria"] = crit
    stats["b2"] = _check_b2(chars, crit) if crit else {"error": "未提供判据"}

    # B3：选片对比（本名 + 新别名 vs 仅本名），差异逐片归到具体别名
    content = env.get("_content") or ""
    if content and args.character:
        stats["selection"] = _selection(content, args.character,
                                        _aliases_of(chars, args.character), env.get("text_type"))
    else:
        stats["selection"] = {"error": "无原文或未指定 --character"}
    return stats


def _load_criteria(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        eprint(f"[criteria] 读不到：{type(exc).__name__}")
        return None


def _aliases_of(chars: list[dict], character: str) -> list[str]:
    for g in chars:
        if g.get("name") == character:
            return list(g.get("aliases") or [])
    return []


def _check_b2(chars: list[dict], crit: dict) -> dict:
    """§10 B2：每人恰 1 组且为「主要」；同组对落同一组；泛称不是 name、不挂在指定人身上。"""
    resolved: dict[str, list[int]] = {}
    for i, g in enumerate(chars):
        resolved.setdefault(g.get("name", ""), []).append(i)
        for a in g.get("aliases") or []:
            resolved.setdefault(a, []).append(i)

    mains, miss_main = [], []
    for name in crit.get("main") or []:
        idxs = resolved.get(name) or []
        if len(idxs) == 1 and chars[idxs[0]].get("importance") == "主要":
            mains.append(name)
        else:
            miss_main.append({"name": name, "groups": len(idxs),
                              "importance": [chars[i].get("importance") for i in idxs]})

    same_ok, same_bad = 0, []
    for pair in crit.get("same_group") or []:
        if len(pair) != 2:
            continue
        a, b = (resolved.get(pair[0]) or []), (resolved.get(pair[1]) or [])
        if a and b and set(a) & set(b):
            same_ok += 1
        else:
            same_bad.append({"pair": pair, "a_groups": a, "b_groups": b})

    generic_names = [g.get("name") for g in chars]
    gen_bad = [x for x in (crit.get("generics") or []) if x in generic_names]
    for person in crit.get("generic_not_on") or []:
        aliases = set(_aliases_of(chars, person))
        for x in (crit.get("generics") or []):
            if x in aliases:
                gen_bad.append(f"{x}∈aliases({person})")

    return {
        "main_ok": len(mains), "main_total": len(crit.get("main") or []),
        "main_miss": miss_main,
        "same_ok": same_ok, "same_total": len(crit.get("same_group") or []),
        "same_bad": same_bad,
        "generics_bad": gen_bad,
        "major_total": sum(1 for g in chars if g.get("importance") == "主要"),
        "major_names": [g.get("name") for g in chars if g.get("importance") == "主要"],
        "aliases": {p: _aliases_of(chars, p) for p in (crit.get("generic_not_on") or [])},
    }


def _selection(content: str, character: str, aliases: list[str], text_type: str = "classic") -> dict:
    """§10 B3/C3：生产选片规则（`[本名] + 别名` 子串筛片 + 全不中则取前 3 片）。

    与 `core/distiller.py` 的 `match_terms` / 子串筛 / `chunks[:3]` 兜底逐条对齐 ——
    规则若改了这里会跟着错，所以只镜像，不发明。只镜非 chat 那条：chat 类型生产走
    `_split_chunks_chat` 且要先做 Layer2 预处理，长书验收用不到，不对它作数。
    """
    dist = Distiller.__new__(Distiller)      # 只用 effective_chunk_size 的纯计算
    dist._chunk_size = EXPECTED_CHUNK_SIZE
    size = dist.effective_chunk_size(text_type)
    chunks = Distiller._split_chunks(content, size)

    def pick(terms: list[str]) -> set[int]:
        hit = {i for i, c in enumerate(chunks) if any(t in c for t in terms)}
        return hit or {0, 1, 2}              # 全不中 → chunks[:3]（生产同款兜底）

    hit_only = pick([character])
    hit_all = pick([character] + aliases)
    attribution: dict[str, int] = {}
    for i in sorted(hit_all - hit_only):
        for t in aliases:
            if t in chunks[i]:
                attribution[t] = attribution.get(t, 0) + 1
    return {"chunk_size": size, "text_type": text_type, "chunks": len(chunks),
            "only": len(hit_only), "with_alias": len(hit_all), "attribution": attribution}


# ══ §10 C：蒸馏 ═══════════════════════════════════════════════════════

def distill_mode(args, dsn: str, token: str, user_id: str, env: dict) -> dict:
    stats: dict = {}
    started_wall = datetime.now(timezone.utc)

    # C1：起任务 + 每 1 s 轮询，记每次状态变化时刻
    t0 = time.monotonic()
    try:
        with http_client(args.base_url, token) as c:
            r = c.post("/api/distill/start", json={
                "text_id": args.text_id, "character_name": args.character, "force": False})
            if r.status_code != 200:
                stats["error"] = f"start HTTP {r.status_code}"
                return stats
            task_id = r.json()["task_id"]
            changes, last = [], None
            while True:
                s = c.get(f"/api/distill/task/{task_id}").json()
                if s.get("status") != last:
                    changes.append((round(time.monotonic() - t0, 1), s.get("status"),
                                    s.get("stage") or "", s.get("progress_pct")))
                    last = s.get("status")
                # DB 四态里 running 是唯一非终态（含 interrupted 也是终态，不再自跑）
                if s.get("status") != "running":
                    break
                if time.monotonic() - t0 > DISTILL_LIMIT_S + 180:
                    changes.append((round(time.monotonic() - t0, 1), "timeout", "", None))
                    break
                time.sleep(POLL_S)
    except Exception as exc:
        stats["error"] = f"{type(exc).__name__}"
        return stats
    stats["elapsed_s"] = round(time.monotonic() - t0, 1)
    stats["changes"] = changes
    stats["final"] = last

    # C2：分阶段耗时（取本用户 usage_stats 行）
    rows = fetch(
        dsn,
        "SELECT action, prompt_tokens, completion_tokens, created_at FROM usage_stats "
        "WHERE user_id = $1 AND created_at >= $2 ORDER BY created_at",
        user_id, started_wall,
    )
    by = {}
    for r in rows:
        by.setdefault(r["action"], []).append(r)
    maps = by.get("distill_map", [])
    reduces = by.get("distill_reduce", [])
    formats = by.get("distill_format", [])

    def rel(row, base) -> float | None:
        return None if base is None else round((row["created_at"] - base).total_seconds(), 1)

    map_base = started_wall if maps else None
    reduce_base = maps[0]["created_at"] if maps else None
    format_base = reduces[-1]["created_at"] if reduces else None
    stats["stages"] = {
        "map_s": rel(maps[0], map_base) if maps else None,
        "batches": [{"s": rel(r, reduce_base), "tok": r["completion_tokens"]} for r in reduces],
        "groups": [rel(r, format_base) for r in formats],
        "usage_rows": len(rows),
    }

    # C3：相关片数与批数（用生产规则现算，不读日志）
    content = env.get("_content") or ""
    if content and args.character:
        sel = _selection(content, args.character,
                         _aliases_of_from_usage(args, token), env.get("text_type"))
        stats["relevant"] = sel
    else:
        stats["relevant"] = {"error": "无原文或未指定 --character"}

    # C4：卡片校验 + 原文命中。带上终态：save_card 按 (text_id, name, user_id) 覆盖写，
    # 同 id 原地更新，所以「卡在不在」分不出是本轮产物还是上一轮失败留下的旧卡；
    # 只有本轮任务真跑到 done，这张卡才当本轮产物看。
    stats["card"] = _check_card(args, token, content, stats["final"])
    return stats


def _aliases_of_from_usage(args, token: str) -> list[str]:
    """别名取自本用户当前的识别名单（`/identify` 的缓存命中）；取不到就按无别名算。"""
    try:
        with http_client(args.base_url, token) as c:
            r = c.post("/api/distill/identify", json={"text_id": args.text_id})
        if r.status_code == 200:
            return _aliases_of((r.json() or {}).get("characters") or [], args.character)
    except Exception as exc:
        eprint(f"[aliases] 取别名失败：{type(exc).__name__}")
    return []


def _check_card(args, token: str, content: str, run_status: str | None) -> dict:
    """§10 C4：`CharacterCard.model_validate` + 顶层字段非空 + 前 5 条原文命中。

    卡片只在本函数内存里存在，不回写库、不落盘；未命中的原样返回给调用方打印
    （§10 C4 明写要贴出来由 Shiyu 判）。

    `run_status` 是本轮任务的终态。失败/中断时库里那张同名旧卡还在，不 gate 就会把
    一次失败的蒸馏报成「卡片校验 通过」。
    """
    if run_status != "done":
        return {"error": f"本轮任务未跑到 done（{run_status}），这张卡不算本轮产物"}
    try:
        with http_client(args.base_url, token) as c:
            r = c.get(f"/api/distill/cards/by-text/{args.text_id}")
        cards = r.json() if r.status_code == 200 else []
    except Exception as exc:
        return {"error": f"{type(exc).__name__}"}
    if not isinstance(cards, list) or not cards:
        return {"error": "没有卡片"}

    raw = None
    for row in cards:                      # 取本次这个角色最新的那张
        cj = row.get("card_json")
        data = json.loads(cj) if isinstance(cj, str) else cj
        if isinstance(data, dict) and data.get("name") == args.character:
            raw = data
            break
    if raw is None:
        return {"error": f"没有 {args.character} 的卡片（共 {len(cards)} 张）"}

    try:
        card = CharacterCard.model_validate(raw)
    except Exception as exc:
        return {"valid": False, "error": f"{type(exc).__name__}"}

    empty = []
    for field in CharacterCard.model_fields:
        v = getattr(card, field)
        v = v.model_dump() if hasattr(v, "model_dump") else v
        if v in ("", [], {}, None):
            empty.append(field)

    def hits(items: list[str]) -> dict:
        items = [str(x) for x in (items or [])][:HITS]
        found = [x for x in items if _clean(x) and _clean(x) in content]
        return {"n": len(items), "hit": len(found),
                "miss": [x for x in items if x not in found]}

    return {"valid": True, "empty": empty,
            "dialogue": hits(card.dialogue_examples),
            "catchphrases": hits(card.speaking_style.catchphrases)}


def _clean(s: str) -> str:
    return s.strip().strip('"').strip("“”「」『』").strip()


# ══ §10 D3：读数模板 ══════════════════════════════════════════════════

def print_env(env: dict) -> None:
    print(f"[环境] config.yaml {env.get('config_yaml')} | chunk_size {env.get('chunk_size')} "
          f"| map_concurrency {env.get('map_concurrency')} | model {env.get('model')} "
          f"| thinking {env.get('thinking')}")
    print(f"[环境] 全文 {env.get('chars', '?')} 字符（期望 {EXPECTED_CHARS}）"
          f" | 识别分片 {env.get('identify_chunks', '?')}（期望 {EXPECTED_IDENTIFY_CHUNKS}）"
          f" | LLM_* 比对 {env.get('llm_env')}")
    bad = [f"{k}={got}（期望 {exp}）" for k, got, exp in (env.get("a1") or []) if got != exp]
    print(f"[环境] A1 对齐: {'全部符合' if not bad else '不符 ' + '; '.join(bad)}")


def print_identify(stats: dict, character: str) -> None:
    print(f"[识别] 整请求 {stats.get('elapsed_s')}s | 逐片 {stats.get('map_s')}s "
          f"| 别名判断 {stats.get('alias_s')}s | 账行数 {stats.get('usage_rows')}")
    if stats.get("error"):
        print(f"[识别] 失败：{stats['error']}")
    b2 = stats.get("b2") or {}
    if b2.get("error"):
        print(f"[识别] B2 判据: {b2['error']}")
    else:
        print(f"[识别] 主要人物 {b2.get('major_total')} 人: {b2.get('major_names')}")
        aliases = b2.get("aliases") or {}
        print(f"[识别] B2 判据: 主要 {b2.get('main_total')}人 {b2.get('main_ok')}/{b2.get('main_total')} "
              f"| 同组 {b2.get('same_ok')}/{b2.get('same_total')} "
              f"| 泛称 {'通过' if not b2.get('generics_bad') else '失败 ' + str(b2.get('generics_bad'))}")
        if b2.get("main_miss"):
            print(f"[识别] B2 未过的人: {json.dumps(b2['main_miss'], ensure_ascii=False)}")
        print(f"[识别] {len(aliases)}人 aliases: {json.dumps(aliases, ensure_ascii=False)}")
    sel = stats.get("selection") or {}
    if sel.get("error"):
        print(f"[识别] 选片: {sel['error']}")
    else:
        print(f"[识别] {character}相关片: 本名+别名 {sel.get('with_alias')} | 仅本名 {sel.get('only')} "
              f"| 分片 {sel.get('chunks')}@{sel.get('chunk_size')}字"
              f" | 差异归因: {json.dumps(sel.get('attribution'), ensure_ascii=False)}")


def print_distill(stats: dict, character: str) -> None:
    st = stats.get("stages") or {}
    batches = " | ".join(f"批{i + 1} {b['s']}s/{b['tok']}tok"
                         for i, b in enumerate(st.get("batches") or [])) or "批 无"
    groups = " | ".join(f"组{i + 1} {g}s" for i, g in enumerate(st.get("groups") or [])) or "组 无"
    rel = stats.get("relevant") or {}
    print(f"[蒸馏-{character}] 相关片 {rel.get('with_alias', rel.get('error', '?'))} "
          f"| 批数 {len(st.get('batches') or [])} | 逐片 {st.get('map_s')}s "
          f"| {batches} | {groups} | 整任务 {stats.get('elapsed_s')}s")
    if stats.get("error"):
        print(f"[蒸馏-{character}] 失败：{stats['error']}")
    print(f"[蒸馏-{character}] 状态变化: {stats.get('changes')}")
    card = stats.get("card") or {}
    if card.get("error"):
        print(f"[蒸馏-{character}] 卡片校验 失败 | {card['error']}")
        return
    d, cp = card.get("dialogue") or {}, card.get("catchphrases") or {}
    print(f"[蒸馏-{character}] 卡片校验 {'通过' if card.get('valid') else '失败'} "
          f"| 空字段 {card.get('empty')} "
          f"| 原文命中 对话 {d.get('hit')}/{d.get('n')} 口癖 {cp.get('hit')}/{cp.get('n')}")
    for label, block in (("对话", d), ("口癖", cp)):
        if block.get("miss"):
            print(f"[蒸馏-{character}] {label}未命中（原样）: {json.dumps(block['miss'], ensure_ascii=False)}")


def print_logs(counts: dict) -> None:
    print("[日志] " + " | ".join(f"{k} {v}" for k, v in counts.items()))


def _evidence_payload(stats: dict) -> dict:
    """给证据产物（入库文件）的投影：只留计数与判定结果，丢掉正文与名单。

    `card.*.miss` 是原文子串、`b2` 的名单与判据跟语料走、`attribution` 是别名
    —— 这些一律不进任何落盘文件（版权语料只留统计量）。D3 的 stdout 读数不受此限：
    那是给人看的报告，不落盘。
    """
    def scrub_hits(block: dict) -> dict:
        out = {k: v for k, v in (block or {}).items() if k != "miss"}
        out["miss_n"] = len((block or {}).get("miss") or [])
        return out

    out = {k: v for k, v in stats.items() if k not in ("chars", "criteria")}
    card = out.get("card")
    if isinstance(card, dict):
        card = {k: v for k, v in card.items() if k not in ("dialogue", "catchphrases")}
        for key in ("dialogue", "catchphrases"):
            if key in (out.get("card") or {}):
                card[key] = scrub_hits(out["card"][key])
        out["card"] = card
    b2 = out.get("b2")
    if isinstance(b2, dict):
        out["b2"] = {k: v for k, v in b2.items()
                     if k not in ("major_names", "aliases", "main_miss", "same_bad")}
        out["b2"]["generics_bad_n"] = len(b2.get("generics_bad") or [])
        out["b2"].pop("generics_bad", None)
    for key in ("selection", "relevant"):
        sel = out.get(key)
        if isinstance(sel, dict):
            out[key] = {k: v for k, v in sel.items() if k != "attribution"}
    return out


def over_limit(stats: dict, counts: dict, env: dict) -> list[str]:
    """§10 D2 的停下条件（命中即报，不调参、不合并）+ A 的对齐门。"""
    why = []
    bad_a1 = [f"{k}={got}" for k, got, exp in (env.get("a1") or []) if got != exp]
    if bad_a1:
        why.append("A1 不符 " + "; ".join(bad_a1))
    if (env.get("chars"), env.get("identify_chunks")) != (EXPECTED_CHARS, EXPECTED_IDENTIFY_CHUNKS):
        why.append(f"文本不符（{env.get('chars')} 字 / {env.get('identify_chunks')} 片）")
    if stats.get("mode") == "identify":
        if (stats.get("elapsed_s") or 0) > IDENTIFY_LIMIT_S:
            why.append(f"识别 {stats['elapsed_s']}s > {IDENTIFY_LIMIT_S:.0f}s")
        b2 = stats.get("b2") or {}
        if b2.get("main_miss") or b2.get("same_bad") or b2.get("generics_bad"):
            why.append("B2 任一条不成立")
    if stats.get("mode") == "distill":
        if (stats.get("elapsed_s") or 0) > DISTILL_LIMIT_S:
            why.append(f"蒸馏 {stats['elapsed_s']}s > {DISTILL_LIMIT_S:.0f}s")
        for i, b in enumerate((stats.get("stages") or {}).get("batches") or []):
            if (b.get("tok") or 0) >= CARD_MAX_TOKENS:
                why.append(f"批{i + 1} completion {b['tok']} ≥ {CARD_MAX_TOKENS}（疑截断）")
    # 429 不再是停下条件：WP14 把 map 并发压到闸以下之后，收敛途中打 429 是**正常信号**，
    # 次数由 `print_logs` 照报，不作判据。分片失败才是 —— 那片结果是空串，会一路污染合并。
    # 计数只在「数过」时才算命中：没给日志时值是「未提供」这个字符串，直接当布尔用
    # 会把「没数过」报成「命中了」。
    for label in ("分片失败", "5xx"):
        n = counts.get(label)
        if isinstance(n, int) and n > 0:
            why.append(f"{label} × {n}")
    return why


# ══ main ══════════════════════════════════════════════════════════════

def main() -> None:
    # Windows 控制台默认 GBK，中文读数会变乱码；与 tests/perf 其他脚本同款处理
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="长书蒸馏验收（蓝图 §10）")
    ap.add_argument("--mode", required=True, choices=("identify", "distill"))
    ap.add_argument("--text-id", required=True, help="本地库里那份长书的 texts.id")
    ap.add_argument("--base-url", default="http://127.0.0.1:7861")
    ap.add_argument("--character", default="", help="distill 模式必填；identify 模式用于 B3 选片")
    ap.add_argument("--criteria", default="", help="identify 模式的判据 JSON（见文件头）")
    ap.add_argument("--app-log", default="", help="服务端日志文件；不给则 D1 报「未提供」")
    ap.add_argument("--evidence-id", default="", help="给了就给 evidence_writer 落一份产物（须先注册）")
    ap.add_argument("--username", default=os.environ.get("ACCEPTANCE_USER", "testadmin"))
    args = ap.parse_args()

    password = os.environ.get("ACCEPTANCE_PASSWORD", "")
    if not password:
        raise SystemExit("缺 ACCEPTANCE_PASSWORD（只经环境变量传，不走命令行）")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("缺 DATABASE_URL（app 与验收脚本必须读同一个库）")
    if args.mode == "identify" and not args.criteria:
        raise SystemExit("identify 模式必须给 --criteria（判据跟语料走，不入库）")

    token, user_id = login(args.base_url, args.username, password)
    env = env_check(dsn, args.text_id)
    print_env(env)

    if args.mode == "identify":
        stats = identify_mode(args, dsn, token, user_id, env)
        print_identify(stats, args.character or "?")
    else:
        if not args.character:
            raise SystemExit("distill 模式必须给 --character")
        stats = distill_mode(args, dsn, token, user_id, env)
        print_distill(stats, args.character)

    counts = count_log(args.app_log or None)
    print_logs(counts)

    why = over_limit(dict(stats, mode=args.mode), counts, env)
    print(f"[门槛] {'命中停下条件: ' + '; '.join(why) if why else '未命中停下条件'}")

    if args.evidence_id:
        write_evidence(args.evidence_id, _evidence_payload(stats),
                       claim=f"§10 {args.mode} 验收读数", script="tests/perf/longbook_acceptance.py",
                       env=f"base_url={args.base_url}；LLM 由被验收栈配置决定", code_sha=code_sha())


if __name__ == "__main__":
    main()
