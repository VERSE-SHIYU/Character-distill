"""本机 rig：384 维旧 chroma 集合重建（只读 plan / 可续跑 rebuild）。

背景：commit db8d1de(2026-06-24) 把 embedder 从本地 SentenceTransformer(384) 换成
DashScope text-embedding-v4(1024)。此后（commit 4a971d1 起）load_existing 对维度不符
抛 CollectionUnusableError → 旧 384 集合场景检索降级、不再静默空。

只重建 **text_{text_id}** —— 场景检索真正读取的集合（chat.py:157 / group.py /
mcp 全走 load_existing(f"text_{text_id}")）。scenes_{card_id} 与 rag_{uuid} 在代码里
无读取方（只写不读 / 每次实例 UUID），重建无收益，plan 会说明并跳过。

判定（plan / rebuild 同口径）：
  REBUILD      dim==384 且 texts 行存在 且 ≥1 张 live 卡引用
  skip-1024    已是 1024 且 count==期望分片（当前 embedder 可用）
  skip-orphan  384 但无 live 卡 / 无源文（烧 embed 无人读）
  skip-empty   空集合 / peek 不出维度

角色语义（关键）：_retrieve_scenes 按 self.card.name $contains characters 过滤
(context_engine.py:266-277)。旧集合元数据状态不一（172239 有 tag / ab_a 无 tag），
重建统一按「当前 live 卡名 + texts.characters_json 别名」复刻 all_characters 打 tag ——
与 web 懒重建口径一致，保证角色过滤检索不被打空。

安全交换（requirement：不删现有集合）：重建 = 内存分片→打 tag→**嵌入全部成功**后，
才 delete 旧集合 + create 同名 + 显式 embeddings 写入（delete 是最后一步原子切换，
嵌入阶段失败则旧集合原样保留）。源文在 sqlite texts.content 是权威副本，极端情况下
add 中断也可重跑原命令收敛。可重跑：已 1024 且 count 匹配的集合自动跳过（断点续跑）。

用法（chroma/duckdb 只能在 Linux 容器读写 —— Windows 原生段错误；bind-mount 宿主仓库）：
  python scripts/rebuild_384_collections.py plan                          # 只读清单（默认）
  python scripts/rebuild_384_collections.py rebuild <text_id> [text_id ...]
  python scripts/rebuild_384_collections.py rebuild --all                 # 全部 REBUILD 集合
  python scripts/rebuild_384_collections.py rebuild --file ids.txt        # 断点续跑

记录：每集合结果 append 到 data/eval_scratch/rebuild_384/run_*.jsonl（gitignored）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.embeddings import DashScopeEmbedding, get_embed_stats, reset_embed_stats  # noqa: E402
from core.rag import RAGEngine  # noqa: E402

DIM_NOW = 1024  # DashScope text-embedding-v4 默认维度；load_existing 比对基准
BATCH = DashScopeEmbedding.MAX_BATCH  # 百炼单次最多 10 条 → HTTP ≈ ceil(chunks/10)
PROBE = "故事的主要角色、性格与关键情节设定"  # 验证查询（逐集合 1 次 embed）

LIVE_CARD = "(deleted_at IS NULL OR deleted_at = '')"


def load_rag_cfg() -> dict:
    """读 config.yaml 的 rag 段（与 web.deps 同源），只取切片参数。"""
    import yaml

    p = _REPO / "config.yaml"
    if not p.exists():
        p = _REPO / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))["rag"]


def _ro_sqlite(db: Path):
    return sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)


def estimate_chunks(content: str, chunk_size: int, chunk_overlap: int) -> int:
    """用真实 RAGEngine._chunk_text 估算重建会写入的切片数（不触发 chroma/embed 构造）。"""
    eng = object.__new__(RAGEngine)
    eng._chunk_size = chunk_size
    eng._chunk_overlap = chunk_overlap
    frags = eng._chunk_text(content or "")
    return len([f for f in frags if f.strip()])


def text_records(db: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        conn = _ro_sqlite(db)
        try:
            rows = conn.execute(
                "SELECT id, title, filename, user_id, char_count, deleted_at, "
                "LENGTH(content) AS content_len FROM texts"
            ).fetchall()
            for r in rows:
                out[r[0]] = {
                    "title": r[1] or "", "filename": r[2] or "",
                    "user_id": r[3] or "", "char_count": r[4],
                    "deleted_at": r[5], "content_len": r[6] or 0,
                }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"[plan] sqlite texts 读取失败（{db}）：{exc}")
    return out


def card_refs(db: Path) -> dict[str, list[dict]]:
    """live 卡按 text_id 分组：[{card_id, name, user_id}]。"""
    out: dict[str, list[dict]] = {}
    try:
        conn = _ro_sqlite(db)
        try:
            rows = conn.execute(
                f"SELECT text_id, id, name, user_id FROM cards "
                f"WHERE text_id <> '' AND {LIVE_CARD}"
            ).fetchall()
            for text_id, cid, name, uid in rows:
                out.setdefault(text_id, []).append(
                    {"card_id": cid, "name": name or "", "user_id": uid or ""}
                )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"[plan] sqlite cards 读取失败（{db}）：{exc}")
    return out


def fetch_content(db: Path, text_id: str) -> str | None:
    try:
        conn = _ro_sqlite(db)
        try:
            row = conn.execute("SELECT content FROM texts WHERE id = ?", (text_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def all_characters_for(db: Path, text_id: str, refs: list[dict]) -> list[dict]:
    """复刻 text_manager._build_all_characters：live 卡名 + characters_json 别名。"""
    names = sorted({c["name"] for c in refs if c["name"]})
    alias_map: dict[str, list[str]] = {}
    try:
        conn = _ro_sqlite(db)
        try:
            row = conn.execute(
                "SELECT characters_json FROM texts WHERE id = ?", (text_id,)
            ).fetchone()
            if row and row[0]:
                for ch in json.loads(row[0]):
                    if isinstance(ch, dict) and ch.get("name"):
                        alias_map[ch["name"]] = ch.get("aliases") or []
        finally:
            conn.close()
    except (sqlite3.Error, json.JSONDecodeError):
        pass
    return [{"name": n, "aliases": alias_map.get(n, [])} for n in names]


def collection_states(chroma_path: Path) -> dict[str, dict]:
    """枚举所有集合：{name: {dim, count}}。dim None=空/peek 失败。"""
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    out: dict[str, dict] = {}
    for c in client.list_collections():
        name = c.name
        try:
            col = client.get_collection(name)
            out[name] = {"dim": RAGEngine._peek_dimension(col), "count": col.count()}
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] 读取集合 {name} 失败：{exc}")
            out[name] = {"dim": None, "count": 0}
    return out


# ── plan：只读清单 ─────────────────────────────────────────────────────────


def build_manifest(chroma_path: Path, db: Path, chunk_size: int, chunk_overlap: int):
    states = collection_states(chroma_path)
    texts = text_records(db)
    cards = card_refs(db)

    text_rows, scenes, rag = [], {"n": 0, "d384": 0, "d1024": 0}, {"n": 0, "d384": 0, "d1024": 0}

    for name, st in sorted(states.items()):
        dim, count = st["dim"], st["count"]
        if name.startswith("text_"):
            text_id = name[len("text_"):]
            text = texts.get(text_id)
            refs = cards.get(text_id, [])
            entry = {
                "name": name, "text_id": text_id, "dim": dim, "count": count,
                "title": text["title"] if text else "",
                "owner": (text or {}).get("user_id", ""),
                "content_len": (text or {}).get("content_len", 0),
                "text_row": text is not None, "cards": refs,
                "status": "", "chunks": None, "embed_calls": None,
            }
            if dim is None or count == 0:
                entry["status"] = "skip-empty"
            elif dim == DIM_NOW:
                entry["status"] = "skip-1024"
            elif dim == 384:
                if not text or not refs:
                    entry["status"] = "skip-orphan-384"
                else:
                    entry["status"] = "REBUILD"
                    content = fetch_content(db, text_id)
                    chunks = estimate_chunks(content, chunk_size, chunk_overlap)
                    entry["chunks"] = chunks
                    entry["embed_calls"] = math.ceil(chunks / BATCH)
            else:
                entry["status"] = f"skip-other-dim-{dim}"
            text_rows.append(entry)
        elif name.startswith("scenes_"):
            scenes["n"] += 1
            scenes["d384" if dim == 384 else "d1024" if dim == DIM_NOW else "n"] += 1
        elif name.startswith("rag_"):
            rag["n"] += 1
            rag["d384" if dim == 384 else "d1024" if dim == DIM_NOW else "n"] += 1
    return text_rows, scenes, rag


def print_manifest(chroma_path: Path, db: Path) -> None:
    cfg = load_rag_cfg()
    text_rows, scenes, rag = build_manifest(
        chroma_path, db, int(cfg["chunk_size"]), int(cfg["chunk_overlap"])
    )

    rebuild = [t for t in text_rows if t["status"] == "REBUILD"]
    print("=== 384 集合重建清单（plan，只读）===\n")
    print("【目标 REBUILD】text_ 集合：dim=384 且含 live 卡引用（用户检索会命中）")
    print(f"{'text_id':<16}{'title/owner':<32}{'live卡':<6}{'旧chunk':<8}"
          f"{'新chunk估算':<11}{'embed HTTP≈'}")
    tot_chunks = tot_calls = 0
    for t in rebuild:
        label = (t["title"] or t["text_id"])[:16] + "/" + t["owner"]
        print(f"{t['text_id']:<16}{label:<32}{len(t['cards']):<6}{t['count']:<8}"
              f"{t['chunks']:<11}{t['embed_calls']}")
        tot_chunks += t["chunks"]
        tot_calls += t["embed_calls"]
    print(f"\n  合计：{len(rebuild)} 个集合，重建约写入 {tot_chunks} 切片，"
          f"DashScope embed HTTP 约 {tot_calls}（batch={BATCH}，单进程共享缓存去重后更少）")

    skipped = [t for t in text_rows if t["status"] != "REBUILD"]
    if skipped:
        print("\n【跳过的 text_ 集合】")
        for t in skipped:
            why = {
                "skip-empty": "空/peek无维（web 懒重建自愈）",
                "skip-1024": f"已 {t['dim']} 维（可用）" + ("·孤儿无卡" if not t["cards"] else ""),
                "skip-orphan-384": "384 但无 live 卡/无源文（烧 embed 无人读）",
            }.get(t["status"], t["status"])
            refs = f"{len(t['cards'])}卡" if t["cards"] else "无卡"
            print(f"  {t['text_id']:<16} {why}（{refs}，旧chunk={t['count']}）")
        known = [t["text_id"] for t in skipped if t["status"] == "skip-1024" and not t["cards"]]
        if known:
            print(f"  注：1024 孤儿（无卡引用）共 {len(known)} 个：{', '.join(known)} → 跳过。")

    print(f"\n【scenes_/rag_ 集合】scenes_ {scenes['n']}（384×{scenes['d384']}，"
          f"1024×{scenes['d1024']}）；rag_ {rag['n']}（384×{rag['d384']}，"
          f"1024×{rag['d1024']}）。代码无读取方（只写不读/实例 UUID）→ 重建无收益，跳过。")
    print("\n用法：确认后 python scripts/rebuild_384_collections.py rebuild --all")
    print("      （或指定 text_id；已 1024 自动跳过，失败可原命令续跑）")


# ── rebuild：交换式重建 + 验证 + 记录 ──────────────────────────────────────


def _env_key() -> tuple[str, str]:
    key = (os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip()
    region = (os.getenv("EMBEDDING_REGION") or "cn").strip()
    return key, region


def rebuild_one(db: Path, chroma_path: Path, cfg: dict, text_id: str,
                key: str, region: str) -> dict:
    """重建单个 text_{text_id}（内存嵌入成功后才删旧建新交换）。返回记录 dict。"""
    name = f"text_{text_id}"
    rec = {"ts": time.strftime("%H:%M:%S"), "text_id": text_id}

    content = fetch_content(db, text_id)
    if not content:
        return {**rec, "ok": False, "status": "no-source", "detail": "texts 无此行/空内容"}

    rag_cfg = dict(cfg)
    if key:
        rag_cfg["embedding_key"] = key
        rag_cfg["embedding_region"] = region
    rag = RAGEngine(rag_cfg, chroma_path=str(chroma_path))
    ef = rag._embedding_function

    filtered = [p for p in rag._chunk_text(content) if p.strip()]
    expected = len(filtered)
    if expected == 0:
        return {**rec, "ok": False, "status": "no-chunks", "detail": "切片后无可用文本"}

    # 已 1024 且分片数匹配 → 无需重建（断点续跑安全闸；count 防截断集合被误跳）
    try:
        old = rag._client.get_collection(name)
        old_dim = RAGEngine._peek_dimension(old)
        old_count = old.count()
    except Exception:
        old_dim, old_count = None, 0
    if old_dim == DIM_NOW and old_count == expected:
        return {**rec, "ok": True, "status": "skip-1024",
                "chunks": expected, "detail": "已是 1024 且分片匹配，跳过"}

    # 打角色 tag（复刻 chat 建索引的 all_characters → characters 元数据）
    refs = card_refs(db).get(text_id, [])
    all_characters = all_characters_for(db, text_id, refs)
    metas = None
    if all_characters:
        metas = [{"characters": rag._tag_characters(c, all_characters)} for c in filtered]
    ids = [f"chunk_{i}" for i in range(expected)]

    t0 = time.time()
    reset_embed_stats()
    try:
        embeddings = ef(filtered)  # 单次喂全量 → _embed_impl 内部按 BATCH 分批 + 共享缓存
    except Exception as exc:  # noqa: BLE001 —— 嵌入失败：旧集合原样保留
        return {**rec, "ok": False, "status": "embed-failed",
                "detail": str(exc)[:300], "secs": round(time.time() - t0, 1)}

    # 交换（delete 是最后一步：新向量已在内存，add 失败也只是同名集合重跑收敛）
    try:
        try:
            rag._client.delete_collection(name)
        except Exception:
            pass  # 不存在也 OK
        col = rag._client.create_collection(name, embedding_function=ef)
        add_kwargs: dict = {"ids": ids, "documents": filtered, "embeddings": embeddings}
        if metas is not None:
            add_kwargs["metadatas"] = metas
        col.add(**add_kwargs)
    except Exception as exc:  # noqa: BLE001
        return {**rec, "ok": False, "status": "swap-failed",
                "detail": str(exc)[:300], "secs": round(time.time() - t0, 1)}

    stats = get_embed_stats()  # 在验证查询(额外 1 次 embed)之前快照
    api_items = stats["api_calls"]
    cache_hits = stats["cache_hits"]

    # 验证（requirement 5）：新引擎 load_existing True + query 非空
    try:
        check = RAGEngine(rag_cfg, chroma_path=str(chroma_path))
        ok_load = check.load_existing(name)
        res = check.query(PROBE)
        ok = bool(ok_load and res)
    except Exception as exc:  # noqa: BLE001
        return {**rec, "ok": False, "status": "verify-failed", "chunks": expected,
                "api_items": api_items, "cache_hits": cache_hits,
                "detail": str(exc)[:300], "secs": round(time.time() - t0, 1)}

    if not ok:
        return {**rec, "ok": False, "status": "verify-failed", "chunks": expected,
                "api_items": api_items, "cache_hits": cache_hits,
                "detail": f"load_existing={ok_load} query_len={len(res) if res is not None else 0}",
                "secs": round(time.time() - t0, 1)}
    return {**rec, "ok": True, "status": "rebuilt", "chunks": expected,
            "api_items": api_items, "cache_hits": cache_hits,
            "http_est": math.ceil(expected / BATCH),
            "detail": f"load=True query={len(res)} 别名{len(all_characters)}",
            "secs": round(time.time() - t0, 1)}


def rebuild(text_ids: list[str], chroma_path: Path, db: Path) -> int:
    cfg = load_rag_cfg()
    key, region = _env_key()
    if not key:
        print("[rebuild] 无 EMBEDDING_API_KEY / DASHSCOPE_API_KEY，拒绝执行", file=sys.stderr)
        return 2
    if not text_ids:
        print("[rebuild] 没有要重建的 text_id", file=sys.stderr)
        return 2

    out_dir = _REPO / "data" / "eval_scratch" / "rebuild_384"
    out_dir.mkdir(parents=True, exist_ok=True)
    logf = out_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    print(f"=== 384 重建（{len(text_ids)} 个）log={logf.name} ===")
    t_start = time.time()
    n_ok = n_fail = 0
    tot_chunks = tot_api = tot_http = 0.0
    for text_id in text_ids:
        rec = rebuild_one(db, chroma_path, cfg, text_id, key, region)
        with logf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if rec["ok"]:
            n_ok += 1
            if rec["status"] == "rebuilt":
                tot_chunks += rec["chunks"]
                tot_api += rec.get("api_items", 0)
                tot_http += rec.get("http_est", 0)
        else:
            n_fail += 1
        line = f"  [{'OK ' if rec['ok'] else 'FAIL'}] text_{text_id:<22}{rec.get('status',''):<12}"
        if rec.get("chunks"):
            line += f"chunks={rec['chunks']} "
        if rec.get("api_items") is not None:
            line += f"api_items={rec['api_items']} hits={rec.get('cache_hits',0)} "
        line += f"{rec.get('secs', 0)}s  {rec.get('detail','')}"
        print(line)
    el = round(time.time() - t_start, 1)
    print(f"\n完成：成功 {n_ok} / 失败 {n_fail}，耗时 {el}s")
    if n_ok:
        print(f"重建写入 {int(tot_chunks)} 切片，实际 provider 嵌入 {int(tot_api)} 条"
              f"（缓存命中不计），HTTP 估算 {int(tot_http)}（log：{logf}）")
    print("重跑本命令即可续跑失败项（已 1024 且分片匹配的自动跳过）。")
    return 0 if n_fail == 0 else 1


def resolve_targets(chroma_path: Path, db: Path, cfg: dict) -> list[str]:
    rows, _, _ = build_manifest(chroma_path, db, int(cfg["chunk_size"]), int(cfg["chunk_overlap"]))
    return [t["text_id"] for t in rows if t["status"] == "REBUILD"]


def main() -> int:
    ap = argparse.ArgumentParser(description="384 维旧 chroma 集合重建（rig，1024 embedder）")
    ap.add_argument("mode", nargs="?", choices=["plan", "rebuild"], default="plan",
                    help="plan=只读清单（默认） rebuild=执行重建")
    ap.add_argument("ids", nargs="*", help="重建的 text_id")
    ap.add_argument("--all", action="store_true", help="rebuild 全部 REBUILD 集合")
    ap.add_argument("--file", help="从文件读 text_id（每行一个，断点续跑）")
    ap.add_argument("--data-dir", default=None, help="覆盖数据目录（默认 <repo>/data）")
    args = ap.parse_args()

    base = Path(args.data_dir) if args.data_dir else _REPO / "data"
    chroma_path = base / "chroma_db"
    db = base / "character_sim.db"
    if not db.exists():
        print(f"[error] sqlite 不存在：{db}", file=sys.stderr)
        return 2

    if args.mode == "plan":
        print_manifest(chroma_path, db)
        return 0

    if args.all:
        text_ids = resolve_targets(chroma_path, db, load_rag_cfg())
    elif args.file:
        text_ids = [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines()
                    if l.strip()]
    else:
        text_ids = args.ids
    return rebuild(text_ids, chroma_path, db)


if __name__ == "__main__":
    sys.exit(main())
