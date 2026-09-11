# -*- coding: utf-8 -*-
"""续跑可达性：一条 distill 行在「删卡但保留行」之后，还能被 find_interrupted_distill 找到吗？

删卡保留行的注释理由是「卡只是反向指针，文本还在就不该清断点，否则重蒸从头烧 API」。
但续跑发现只匹配 status='interrupted'。本脚本逐状态现跑现测，看这个理由是否成立。

用法：
    python e2e/scratch/distill_resume_reachability.py            # sqlite
    DATABASE_URL=... python e2e/scratch/distill_resume_reachability.py pg
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


async def main(kind: str):
    from storage.sqlite_store import SQLiteStore

    if kind == "pg":
        from storage.postgres_store import PostgresStore
        store = PostgresStore(os.environ["DATABASE_URL"])
    else:
        store = SQLiteStore(str(Path("data") / f"resume_reach_{uuid.uuid4().hex}.db"))

    uid = f"u_{uuid.uuid4().hex[:8]}"
    await store.create_user(uid, uid, "x", f"{uid}@t.local")

    print(f"=== store={kind} ===")
    print(f"  {'删卡时任务状态':<20} {'续跑发现能命中该行?':<20} 断点片是否可复用")
    print("  " + "-" * 62)
    for status in ("running", "done", "error", "interrupted"):
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        dtid = f"dt_{uuid.uuid4().hex}"
        await store.save_text(tid, "src.txt", "正文内容", user_id=uid)
        await store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid)
        await store.create_distill_task(
            dtid, uid, tid, "张三", status=status, progress_pct=0,
            message="m", card_id=cid, awakening="", chunk_size=100, overlap=0,
            text_fingerprint="fp",
        )
        await store.save_distill_chunk(dtid, 0, "分片结果", "chunkfp")
        await store.purge_card(cid)          # 反正常规迭代：删卡，保留行

        hit = await store.find_interrupted_distill(uid, tid, "张三")
        ok = hit is not None and hit["task_id"] == dtid
        print(f"  {status:<20} {'是' if ok else '否':<20} {'可复用' if ok else '整批重跑（断点白留）'}")


if __name__ == "__main__":
    run(main(sys.argv[1] if len(sys.argv) > 1 else "sqlite"))
