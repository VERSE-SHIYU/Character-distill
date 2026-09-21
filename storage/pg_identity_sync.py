"""PG identity 序列对齐 —— 全仓唯一实现。

**要修的是什么**：显式带 id 的灌入（一次性导入脚本的 `OVERRIDING SYSTEM VALUE`）**不会**
推进 identity 序列。序列于是停在起点，表里的 id 却已排到 N —— 之后每一条 `nextval` 都
撞已有主键。SG 的 `usage_stats` 就是这么变成「全员用量为 0」的：写入被主键拒绝，
异常又被 `core/utils.try_record_usage` 吞掉（见 AGENTS.md 缺陷 93）。

**做什么**：把每个 identity 序列推到「不落后于本表 max(id)」。只前进不后退，空表不动，
不维护表清单 —— 列从 `pg_catalog` 现查，新加的 identity 表不改代码即被纳入。

**不加锁的依据**：唯一的生产调用点在 `PostgresStore._ensure_initialized` 里，那条路径本身
持 `_init_lock`，且本进程所有读写都先经 `_ensure_initialized()` —— 对齐跑完之前，本进程
不会发出任何写；每地只跑一个 app 容器，不存在第二个写者。故不需要额外的并发保护。
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-not-found]

# 现查所有 identity 列（`attidentity` = 'a' 恒生成 / 'd' 默认生成），连同它的序列。
# 序列名经 `pg_get_serial_sequence` 拿到 regclass 再拆回 schema/表名，避免解析文本。
_IDENTITY_COLUMNS_SQL = """
SELECT
    n.nspname  AS table_schema,
    c.relname  AS table_name,
    a.attname  AS column_name,
    sn.nspname AS seq_schema,
    sc.relname AS seq_name
FROM pg_attribute a
JOIN pg_class     c  ON c.oid = a.attrelid
JOIN pg_namespace n  ON n.oid = c.relnamespace
JOIN pg_class     sc ON sc.oid = pg_get_serial_sequence(
                            format('%I.%I', n.nspname, c.relname), a.attname)::regclass
JOIN pg_namespace sn ON sn.oid = sc.relnamespace
WHERE a.attidentity IN ('a', 'd')
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1, 2, 3
"""


def _quote(ident: str) -> str:
    """把 catalog 里取回的标识符包成带引号的形式。"""
    return '"' + ident.replace('"', '""') + '"'


async def align_identity_sequences(
    conn: asyncpg.Connection,
) -> list[tuple[str, str, int, int]]:
    """把每个 identity 序列推到 nextval = max(id) + 1，返回被前移的表。

    返回值每项是 `(table_name, column_name, nextval_before, nextval_after)`；
    无变化则为空列表。**被前移的逐个打印一行**，没动的安静跳过。

    三条判据（都在函数体内，不靠调用方把关）：
      - 只前进：序列已经领先于 max(id) 时原样留着，绝不回退；
      - 空表不动：`max(id) IS NULL` 时连读都不读它 —— 空表的序列位置是没有信息量的；
      - 幂等：第二次跑时 `current_next` 已等于 `max_id + 1`，不再命中前移条件。

    **本函数不加并发保护**，依据见模块 docstring：调用点全在「本进程尚无写入」的窗口里，
    且每地只有一个写者。
    """
    moved: list[tuple[str, str, int, int]] = []

    for rec in await conn.fetch(_IDENTITY_COLUMNS_SQL):
        table = f'{_quote(rec["table_schema"])}.{_quote(rec["table_name"])}'
        sequence = f'{_quote(rec["seq_schema"])}.{_quote(rec["seq_name"])}'
        column = _quote(rec["column_name"])

        # 一条语句里同时取表侧事实与序列侧事实：序列本身是一行一列的关系，可直接当表读。
        row = await conn.fetchrow(
            f"SELECT (SELECT max({column}) FROM {table}) AS max_id,"
            f" s.last_value, s.is_called FROM {sequence} s"
        )

        max_id = row["max_id"]
        if max_id is None:
            continue  # 空表不动

        # nextval 的算术：is_called 为假时序列还没发出过值，下一个就是 last_value 本身。
        next_before = row["last_value"] + 1 if row["is_called"] else row["last_value"]
        if next_before > max_id:
            continue  # 只前进不后退（含本来就已经健康的）

        await conn.execute(
            "SELECT setval($1::text::regclass, $2::bigint, true)", sequence, max_id
        )
        next_after = max_id + 1
        moved.append((rec["table_name"], rec["column_name"], next_before, next_after))
        print(
            f"[pg-identity] {rec['table_name']}.{rec['column_name']}: "
            f"{rec['seq_name']} nextval {next_before} -> {next_after}"
        )

    return moved
