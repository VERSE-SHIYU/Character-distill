# -*- coding: utf-8 -*-
"""续跑可达性：一条 distill 行在「删卡但保留行」之后，还能被 find_interrupted_distill 找到吗？

删卡保留行的注释理由是「卡只是反向指针，文本还在就不该清断点，否则重蒸从头烧 API」。
但续跑发现只匹配 status='interrupted'。本脚本逐状态现跑现测，看这个理由是否成立。

**一次跑两个 store**，缺 `DATABASE_URL` 直接拒绝运行（不许静默只跑一半）。

用法：
    DATABASE_URL=postgresql://... python tests/perf/distill_resume_reachability.py

产物：写入出口落 `docs/evidence/distill-resume-reachability.json`。
无 LLM 调用、无外部服务，纯确定性。
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

EVIDENCE_ID = "distill-resume-reachability"
ENV = ("本地 sqlite（临时库文件）+ 一次性 PG（postgres:16-alpine，"
       "POSTGRES_HOST_AUTH_METHOD=trust）；无 LLM 调用、无网络依赖，确定性")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def one_store(kind: str) -> list[dict]:
    from storage.sqlite_store import SQLiteStore

    if kind == "pg":
        from storage.postgres_store import PostgresStore
        store = PostgresStore(os.environ["DATABASE_URL"])
    else:
        store = SQLiteStore(str(Path("data") / f"resume_reach_{uuid.uuid4().hex}.db"))

    uid = f"u_{uuid.uuid4().hex[:8]}"
    await store.create_user(uid, uid, "x", f"{uid}@t.local")

    print(f"=== store={kind} ===", flush=True)
    print(f"  {'删卡时任务状态':<20} {'续跑发现能命中该行?':<20} 断点片是否可复用", flush=True)
    print("  " + "-" * 62, flush=True)

    rows: list[dict] = []
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
        rows.append({"status_at_card_delete": status, "resume_hits_row": ok,
                     "checkpoint_reusable": ok})
        print(f"  {status:<20} {'是' if ok else '否':<20} "
              f"{'可复用' if ok else '整批重跑（断点白留）'}", flush=True)
    return rows


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit(
            "必须设 DATABASE_URL —— 两个 store 逐格对照是本档的一部分，不许静默只跑一半。")
    stores = {"sqlite": run(one_store("sqlite")), "pg": run(one_store("pg"))}
    hits = sorted(r["status_at_card_delete"] for r in stores["sqlite"] if r["resume_hits_row"])
    claim = (f"删卡保留行后，find_interrupted_distill 仅命中 status={hits or '（无）'} 的行"
             f"（其余状态整批重跑）；sqlite 与 PG "
             f"{'逐格相同' if stores['sqlite'] == stores['pg'] else '不一致'}")
    out = write_evidence(
        EVIDENCE_ID, {"probe": "resume_reachability", "stores": stores}, claim=claim,
        script="tests/perf/distill_resume_reachability.py", env=ENV, code_sha=code_sha(),
        reproduce="DATABASE_URL=<一次性 PG> python tests/perf/distill_resume_reachability.py")
    print(f"EVIDENCE {out.as_posix()}")


if __name__ == "__main__":
    main()
