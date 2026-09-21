# -*- coding: utf-8 -*-
"""088/021 的存量回填：旧形态库启动一次，副本关系归位；**且只跑那一次**。

旧形态 = `published_from` 列不存在，作者的发布副本靠 `forked_from` 指着草稿
（`copy.forked_from = draft.id` 且 `copy.user_id = draft.user_id`）。088 那句
`ALTER TABLE cards ADD COLUMN published_from` 之后的整段回填并收敛，就是给这种库补账。

**本案的根本命题是「回填只跑一次」**（T2）。PG 侧没有「已应用」账本、每轮 init 全量重放，
SQLite 侧靠 `_apply_migration` 的「本文件每个 ADD COLUMN 的列都已存在 → 整份跳过」。
两边都靠「列已存在」这一个谓词，所以回填必须紧贴在加列那一刻，不能另开文件。
至于为什么不能重跑：fork 路由允许 fork 自己的公开卡（`web/routers/market.py` 的
`POST /{card_id}/fork`），`fork_card` 只写 `forked_from`，于是新代码会写出一条**同属主
`forked_from`** 的合法行 —— 回填重跑就把它改判成发布副本，同一张副本被 fork 两次再撞
唯一索引，启动直接失败。

夹具不经生产 API 造：`published_from` 一旦存在，store 的写入路径就不会再产出「旧形态行」，
所以只能建库后改回旧形态（`_revert_cards_to_pre_088`）。改的是**库的形状**（列、约束、索引），
行仍由 store 正常写入，避免手插行踩 NOT NULL。

PG 同形用例见 `tests/test_postgres_store.py::TestPgPublishedFromBackfill`（含 T4 双侧一致）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from storage.sqlite_store import (
    SQLiteStore,
    _cards_rebuild_columns,
    _fk_disabled,
    _published_copy_of,
)


# ── 夹具：造一个旧形态库 ──────────────────────────────────────────────────────

@pytest.fixture
def owner():
    return f"user_{uuid.uuid4().hex[:8]}"


async def _revert_cards_to_pre_088(store) -> None:
    """把建好的库改回旧形态：cards 去掉 `published_from` 列、复合外键与 090 的索引。

    只能重建表：实测 SQLite 3.49.1 直接 `ALTER TABLE cards DROP COLUMN published_from`
    报 `unknown column "published_from" in foreign key definition`（表级复合外键引用了它）。
    重建手法与 `_rebuild_cards_published_from` 逐字同款（同一套列清单现算 + 关外键），
    只是方向相反：那边加约束，这边把列与约束一起去掉。
    """
    async with await store._connect() as conn:
        # 次序不能换：先 DROP INDEX 会报 `database table is locked`（实测 SQLite 3.49.1）
        # —— cards 上那个 AFTER DELETE 触发器还活着时，同一张表的索引删不掉。
        await conn.execute("DROP TRIGGER IF EXISTS trg_cards_clear_published_from")
        await conn.execute("DROP INDEX IF EXISTS cards_published_from_live_uniq")
        cursor = await conn.execute("PRAGMA table_info(cards)")
        info = [r for r in await cursor.fetchall() if r[1] != "published_from"]
        cursor = await conn.execute("PRAGMA foreign_key_list(cards)")
        fks = await cursor.fetchall()
        # 复合外键在 PRAGMA 里是两行（seq 0/1），按 id 成组去掉 —— 只按 frm 过滤会漏掉
        # `user_id` 那一行，剩下的半条 FK 建表时直接报错。
        dropped = {r[0] for r in fks if r[3] == "published_from"}
        fks = [r for r in fks if r[0] not in dropped]
        cursor = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'cards' "
            "AND sql IS NOT NULL")
        idx_sql = [r[0] for r in await cursor.fetchall()]

        names = [r[1] for r in info]
        collist = ", ".join(f'"{c}"' for c in names)
        async with _fk_disabled(conn):
            await conn.executescript(f"""
                CREATE TABLE cards_mig (
                    {",\n    ".join(_cards_rebuild_columns(info, fks))}
                );
                INSERT INTO cards_mig ({collist}) SELECT {collist} FROM cards;
                DROP TABLE cards;
                ALTER TABLE cards_mig RENAME TO cards;
            """)
            for sql in idx_sql:
                await conn.execute(sql)
            await conn.commit()


_SET_OLD_SHAPE = {
    "qmark": ("UPDATE cards SET forked_from = ?, user_id = ?, visibility = 'public', likes = ? "
              "WHERE id = ?"),
    "dollar": ("UPDATE cards SET forked_from = $1, user_id = $2, visibility = 'public', likes = $3 "
               "WHERE id = $4"),
}


async def seed_old_shape_copies(store, owner, likes_list, prefix, style="qmark"):
    """一张草稿 + 若干张**旧形态**的存活发布副本（`forked_from` 指着草稿）。

    每张副本各有自己的 text_id：启动去重那条 DELETE 按 `(forked_from, user_id, text_id)`
    分区，共用 text_id 的话多张副本会被折叠成一张，T3 就成了空夹具。

    id 用 `prefix` 定死（`{prefix}_draft` / `{prefix}_copy{i}`）而不是随机：T4 要拿两侧
    的读数逐行比，得先让两侧行同名。`style` 只差占位符写法（SQLite `?` / PG `$n`）。

    只借 store 的写路径造行（`save_text` / `save_card`），再**裸 SQL** 把它改成旧形态 ——
    行本身不必躲 NOT NULL，只有 `forked_from` / `likes` / `visibility` 三列是旧语义下的值。
    """
    draft = f"{prefix}_draft"
    await store.save_card(draft, await _text(store, owner), "张三",
                          json.dumps({"name": "张三"}), user_id=owner)
    copies = []
    for i, likes in enumerate(likes_list):
        copy_id = f"{prefix}_copy{i}"
        await store.save_card(copy_id, await _text(store, owner), "张三",
                              json.dumps({"name": "张三"}), user_id=owner)
        copies.append(copy_id)
        params = (draft, owner, likes, copy_id)
        async with await store._connect() as conn:
            # 两个驱动的传参约定不同：aiosqlite 收一个序列，asyncpg 收展开的位置参数。
            if style == "dollar":
                await conn.execute(_SET_OLD_SHAPE[style], *params)
            else:
                await conn.execute(_SET_OLD_SHAPE[style], params)
    return draft, copies


async def _text(store, user_id) -> str:
    text_id = f"txt_{uuid.uuid4().hex}"
    await store.save_text(text_id, "src.txt", "content", user_id=user_id)
    return text_id


async def _old_shape_store(tmp_path, seed) -> tuple[str, dict]:
    """建库 → `seed(store)` 造行 → 改回旧形态。返回 (db_path, seed 的读数)。"""
    db_path = str(tmp_path / f"old_{uuid.uuid4().hex}.db")
    store = SQLiteStore(db_path)
    await store._ensure_initialized()
    reading = await seed(store)
    await _revert_cards_to_pre_088(store)
    return db_path, reading


# 读一律走**裸 sqlite3**，不走 `SQLiteStore._connect()`：后者会先跑 `_ensure_initialized`，
# 于是「init 之前」的任何读数都会顺手把迁移应用掉 —— 夹具前提和断言一起被改，测试恒绿。
def _rows(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _scalar(db_path, sql, args=()):
    rows = _rows(db_path, sql, args)
    return rows[0][0] if rows else None


async def run_old_shape_scenario(tmp_path, owner, likes_list, prefix) -> dict:
    """旧形态夹具 → init 一次 → 返回 `{card_id: (published_from, forked_from)}`。

    给 T4 用（PG 侧那一半在 `tests/test_postgres_store.py`）：两侧跑同一组夹具，逐行比。
    """
    db_path, (draft, copies) = await _old_shape_store(
        tmp_path, lambda s: seed_old_shape_copies(s, owner, likes_list, prefix))
    await SQLiteStore(db_path)._ensure_initialized()
    ids = [draft, *copies]
    marks = ", ".join("?" * len(ids))
    return {r[0]: (r[1], r[2]) for r in _rows(
        db_path, f"SELECT id, published_from, forked_from FROM cards WHERE id IN ({marks})",
        tuple(ids))}


# ── T1 ───────────────────────────────────────────────────────────────────────

class TestOldShapeBackfill:
    """T1：旧形态库启动一次 → 副本的 `published_from` 指向草稿、`forked_from` 清空。"""

    async def test_startup_moves_the_copy_relation_onto_published_from(self, tmp_path, owner):
        db_path, (draft, copies) = await _old_shape_store(
            tmp_path, lambda s: seed_old_shape_copies(s, owner, [0], "t1"))
        copy_id = copies[0]

        # 改回旧形态后先确认前提：列真的不在了，否则下面测的是「本来就对」的库。
        assert "published_from" not in {
            r[1] for r in _rows(db_path, "PRAGMA table_info(cards)")}, \
            "夹具没把列去掉，本用例会恒绿"

        await SQLiteStore(db_path)._ensure_initialized()

        assert _scalar(db_path, "SELECT published_from FROM cards WHERE id = ?", (copy_id,)) \
            == draft, "旧形态库启动后，副本的 published_from 没指向草稿"
        assert _scalar(db_path, "SELECT forked_from FROM cards WHERE id = ?", (copy_id,)) \
            == "", "副本的 forked_from 没清空 —— 发布副本仍被当成他人 fork"
        assert _scalar(db_path, "SELECT forked_from FROM cards WHERE id = ?", (draft,)) \
            == "", "草稿被误写成 fork"


# ── T2（本案的根本命题）──────────────────────────────────────────────────────

class TestBackfillRunsExactlyOnce:
    """T2：回填后新建的「同属主自我 fork」在下次启动时不得被改判成发布副本。"""

    async def test_self_fork_created_after_backfill_survives_the_next_startup(
            self, tmp_path, owner):
        db_path, (draft, copies) = await _old_shape_store(
            tmp_path, lambda s: seed_old_shape_copies(s, owner, [0], "t1"))
        copy_id = copies[0]

        first = SQLiteStore(db_path)
        await first._ensure_initialized()
        assert _scalar(db_path, "SELECT published_from FROM cards WHERE id = ?", (copy_id,)) \
            == draft, "夹具的回填没成功，本用例会恒绿"

        # 回填之后才产生的合法自我 fork：fork 自己的公开卡（market.py 的路由就允许这么干）。
        self_fork = f"card_{uuid.uuid4().hex}"
        assert await first.fork_card(copy_id, self_fork, owner, None) is not None, \
            "夹具没 fork 出来，本用例会恒绿"

        # 新实例 = 再次 init（同一个实例的 `_initialized` 会短路第二次调用）。
        await SQLiteStore(db_path)._ensure_initialized()

        assert _scalar(db_path, "SELECT forked_from FROM cards WHERE id = ?", (self_fork,)) \
            == copy_id, "二次启动把合法自我 fork 的 forked_from 改掉了 —— 回填又跑了一遍"
        assert _scalar(db_path, "SELECT published_from FROM cards WHERE id = ?", (self_fork,)) \
            is None, "二次启动把合法自我 fork 改判成了发布副本"


# ── T3 ───────────────────────────────────────────────────────────────────────

class TestConvergencePicksExactlyOne:
    """T3：同一草稿三张存活副本 → 恰一张拿到 `published_from`，其余落回普通 fork。"""

    async def test_exactly_one_copy_wins_and_the_losers_keep_their_rows(
            self, tmp_path, owner):
        likes = [1, 9, 5]
        db_path, (draft, copies) = await _old_shape_store(
            tmp_path, lambda s: seed_old_shape_copies(s, owner, likes, "t3"))
        winner, losers = copies[1], [copies[0], copies[2]]  # likes 最大的那张

        await SQLiteStore(db_path)._ensure_initialized()

        assert _scalar(db_path, "SELECT published_from FROM cards WHERE id = ?", (winner,)) \
            == draft, "收敛没留下任何一张"
        assert _scalar(db_path, "SELECT forked_from FROM cards WHERE id = ?", (winner,)) \
            == "", "赢家没清 forked_from"
        for loser in losers:
            assert _scalar(db_path, "SELECT published_from FROM cards WHERE id = ?",
                           (loser,)) is None, f"落选副本 {loser} 也被写成发布副本"
            assert _scalar(db_path, "SELECT forked_from FROM cards WHERE id = ?", (loser,)) \
                == draft, f"落选副本 {loser} 被改动了 —— 它该原样成为普通 fork"
            assert _scalar(db_path, "SELECT COUNT(*) FROM cards WHERE id = ?", (loser,)) \
                == 1, "收敛把落选副本删了"
        assert _scalar(
            db_path, "SELECT COUNT(*) FROM cards WHERE published_from = ? AND deleted_at IS NULL",
            (draft,)) == 1, "同一草稿有多张存活副本（唯一索引没建上或没起作用）"


# ── 等价锁：迁移里的 SQL 谓词 ≡ 代码里的关系谓词 ─────────────────────────────

class TestBackfillPredicateMatchesTheRelationPredicate:
    """回填选中的行集，必须与代码里那份关系谓词在**同一批旧形态数据**上选中的行集相等。

    这两份谓词各写在一处、彼此看不见：SQL 在 `storage/migrations/088_published_from.sql`
    （PG 侧 021）的 UPDATE 里，Python 在 `storage/sqlite_store.py::_PUBLISHED_COPY_OF`。
    它们一旦分叉（例：谁把 `deleted_at IS NULL` 去掉），回填会把「已删副本」也认成发布副本，
    而代码不认 —— 没有任何测试会红。这条锁就是那个缺口：拿同一批数据分别问两边，比行集。
    """

    async def test_the_migrations_selection_equals_the_python_predicate(self, tmp_path, owner):
        other = f"user_{uuid.uuid4().hex[:8]}"

        async def seed(store):
            draft, copies = await seed_old_shape_copies(store, owner, [0], "eq")
            wanted = copies[0]
            # 落选者 1：他人 fork（同 `forked_from`，但不同属主）—— 两边都不该选它。
            foreign = f"card_{uuid.uuid4().hex}"
            await store.save_card(foreign, await _text(store, other), "张三",
                                  json.dumps({"name": "张三"}), user_id=other)
            # 落选者 2：同属主的**已软删**副本 —— 关系谓词带 `deleted_at IS NULL`，两边都不选。
            gone = f"card_{uuid.uuid4().hex}"
            await store.save_card(gone, await _text(store, owner), "李四",
                                  json.dumps({"name": "李四"}), user_id=owner)
            async with await store._connect() as conn:
                await conn.execute(
                    "UPDATE cards SET forked_from = ?, user_id = ?, visibility = 'public', "
                    "deleted_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                    (draft, owner, gone))
                for card_id in (foreign,):
                    await conn.execute(
                        "UPDATE cards SET forked_from = ?, user_id = ?, visibility = 'public' "
                        "WHERE id = ?", (draft, other, card_id))
            return wanted, foreign, gone

        db_path, (wanted, foreign, gone) = await _old_shape_store(tmp_path, seed)

        # 两份谓词用的列不同（SQL 写 `forked_from`、Python 写 `published_from`），所以先
        # 把旧形态数据搬进一张**临时表**，那里 `published_from := forked_from` —— 即
        # 「若这一行是发布副本，它长什么样」。之后就能拿 Python 谓词真去问这批行。
        # 搬的是 init 之前的快照：回填一旦清掉赢家的 `forked_from`，原库上就再也问不出来了。
        snapshot = _rows(db_path, "SELECT id, forked_from, user_id, deleted_at FROM cards")
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, published_from TEXT, "
                            "user_id TEXT, deleted_at TEXT)")
            scratch.executemany("INSERT INTO cards VALUES (?, ?, ?, ?)",
                                [(i, f or None, u, d) for i, f, u, d in snapshot])
            predicate = _published_copy_of("c", "d")
            picked = {r[0] for r in scratch.execute(
                f"SELECT c.id FROM cards c JOIN cards d ON d.id = c.published_from "
                f"WHERE {predicate} AND d.user_id = c.user_id")}
        finally:
            scratch.close()

        assert picked == {wanted}, f"夹具坏了：谓词选中的是 {picked}，期望只有 {wanted}"

        await SQLiteStore(db_path)._ensure_initialized()

        backfilled = {r[0] for r in _rows(
            db_path, "SELECT id FROM cards WHERE published_from IS NOT NULL")}
        assert backfilled == picked, (
            "回填选中的行集与代码里的关系谓词不一致 —— 两份谓词分叉了："
            f"回填选中 {backfilled}，谓词选中 {picked}")
