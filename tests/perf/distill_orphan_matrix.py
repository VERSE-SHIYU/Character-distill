# -*- coding: utf-8 -*-
"""F 孤儿行 matrix：每条删除路径删完后，distill_tasks / distill_chunks 各剩几行。

现跑现数。**一次跑两个 store** —— 本档的结论就是「sqlite 与 PG 逐格相同」，
只跑一个 store 这个结论就不成立，故缺 `DATABASE_URL` 直接拒绝运行（不许静默只跑一半）。

用法：
    DATABASE_URL=postgresql://... python tests/perf/distill_orphan_matrix.py

产物：写入出口落 `docs/evidence/distill-orphan-matrix.json`。
无 LLM 调用、无外部服务，纯确定性 —— 重跑得到同一张表。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "tests" / "perf"))

from evidence_writer import code_sha, write_evidence  # noqa: E402

EVIDENCE_ID = "distill-orphan-matrix"
ENV = ("本地 sqlite（临时库文件）+ 一次性 PG（postgres:16-alpine，"
       "POSTGRES_HOST_AUTH_METHOD=trust）；无 LLM 调用、无网络依赖，确定性")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _seed(store, uid):
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    dtid = f"dt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "正文内容", user_id=uid)
    await store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid)
    await store.create_distill_task(
        dtid, uid, tid, "张三", status="running", progress_pct=0,
        message="m", card_id=cid, awakening="", chunk_size=100, overlap=0,
        text_fingerprint="fp",
    )
    await store.save_distill_chunk(dtid, 0, "分片结果", "chunkfp")
    return tid, cid, dtid


async def _count(store, dtid):
    task = await store.get_distill_task_unscoped(dtid)
    chunks = await store.get_distill_chunks(dtid)
    return (1 if task else 0), len(chunks)


async def one_store(kind: str) -> list[dict]:
    from storage.sqlite_store import SQLiteStore

    if kind == "pg":
        from storage.postgres_store import PostgresStore
        store = PostgresStore(os.environ["DATABASE_URL"])
    else:
        store = SQLiteStore(str(Path("data") / f"orphan_matrix_{uuid.uuid4().hex}.db"))

    uid = f"u_{uuid.uuid4().hex[:8]}"
    await store.create_user(uid, uid, "x", f"{uid}@t.local")

    rows: list[dict] = []

    async def case(name, action):
        tid, cid, dtid = await _seed(store, uid)
        await action(tid, cid, dtid)
        t, c = await _count(store, dtid)
        rows.append({"case": name, "distill_tasks": t, "distill_chunks": c})
        print(f"  {name:<34} distill_tasks={t}  distill_chunks={c}", flush=True)

    print(f"=== store={kind} ===", flush=True)
    await case("(基线：不删任何东西)", lambda *a: asyncio.sleep(0))
    await case("delete_text（软删）", lambda t, c, d: store.delete_text(t))
    await case("hard_delete_text(keep_cards=False)", lambda t, c, d: store.hard_delete_text(t, keep_cards=False))
    await case("hard_delete_text(keep_cards=True)", lambda t, c, d: store.hard_delete_text(t, keep_cards=True))
    await case("delete_card（软删卡）", lambda t, c, d: store.delete_card(c))
    await case("purge_card（永久删卡）", lambda t, c, d: store.purge_card(c))
    await case("detach_text_cards（解绑不删）", lambda t, c, d: store.detach_text_cards(t))
    await case("delete_user（删用户）", lambda t, c, d: store.delete_user(uid))
    return rows


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit(
            "必须设 DATABASE_URL —— 本档的结论是「sqlite 与 PG 逐格相同」，"
            "缺一个 store 结论就不成立，不许静默只跑一半。")
    stores = {"sqlite": run(one_store("sqlite")), "pg": run(one_store("pg"))}
    same = stores["sqlite"] == stores["pg"]
    claim = (f"8 条删除路径删完后 distill_tasks / distill_chunks 残留矩阵"
             f"（{len(stores['sqlite'])} 格）；sqlite 与 PG {'逐格相同' if same else '不一致'}")
    out = write_evidence(
        EVIDENCE_ID, {"probe": "orphan_matrix", "stores": stores}, claim=claim,
        script="tests/perf/distill_orphan_matrix.py", env=ENV, code_sha=code_sha(),
        reproduce="DATABASE_URL=<一次性 PG> python tests/perf/distill_orphan_matrix.py")
    print(f"EVIDENCE {out.as_posix()}")
    if not same:
        print("⚠️ 两个 store 不一致 —— 见产物 stores 两节", file=sys.stderr)


if __name__ == "__main__":
    main()
