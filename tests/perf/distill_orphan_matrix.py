# -*- coding: utf-8 -*-
"""F 孤儿行 matrix：每条删除路径删完后，distill_tasks / distill_chunks 各剩几行。

现跑现数，双 store 同跑。用法：
    python e2e/scratch/distill_orphan_matrix.py            # sqlite
    DATABASE_URL=... python e2e/scratch/distill_orphan_matrix.py pg
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "web"))


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
    task = await store.get_distill_task(dtid)
    chunks = await store.get_distill_chunks(dtid)
    return (1 if task else 0), len(chunks)


async def main(kind: str):
    from storage.sqlite_store import SQLiteStore

    if kind == "pg":
        from storage.postgres_store import PostgresStore
        store = PostgresStore(os.environ["DATABASE_URL"])
    else:
        store = SQLiteStore(str(Path("data") / f"orphan_matrix_{uuid.uuid4().hex}.db"))

    uid = f"u_{uuid.uuid4().hex[:8]}"
    await store.create_user(uid, uid, "x", f"{uid}@t.local")

    async def case(name, action):
        tid, cid, dtid = await _seed(store, uid)
        await action(tid, cid, dtid)
        t, c = await _count(store, dtid)
        print(f"  {name:<34} distill_tasks={t}  distill_chunks={c}")

    print(f"=== store={kind} ===")
    await case("(基线：不删任何东西)", lambda *a: asyncio.sleep(0))
    await case("delete_text（软删）", lambda t, c, d: store.delete_text(t))
    await case("hard_delete_text(keep_cards=False)", lambda t, c, d: store.hard_delete_text(t, keep_cards=False))
    await case("hard_delete_text(keep_cards=True)", lambda t, c, d: store.hard_delete_text(t, keep_cards=True))
    await case("delete_card（软删卡）", lambda t, c, d: store.delete_card(c))
    await case("purge_card（永久删卡）", lambda t, c, d: store.purge_card(c))
    await case("detach_text_cards（解绑不删）", lambda t, c, d: store.detach_text_cards(t))
    await case("delete_user（删用户）", lambda t, c, d: store.delete_user(uid))


if __name__ == "__main__":
    run(main(sys.argv[1] if len(sys.argv) > 1 else "sqlite"))
