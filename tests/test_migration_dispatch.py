"""迁移分发与错误处理的**形态锁**（缺陷 15）。

两条不变量，都锁「症状不可能发生」，不是「这次修好了」：

1. `storage/migrations/` 下每个 `.sql` 要么在 sqlite 的迁移次序表里，要么在执行器
   （`storage/sqlite_store.py`）声明的 `_MIGRATIONS_NOT_APPLIED` 里带理由。此前次序是 74
   个手写块，**加一个迁移文件忘了接线没有任何东西报警** —— 先例
   `079_remote_user_profiles.sql` 漏了一整个版本，SQLite 新库因此缺表。

   豁免的事实源在执行器、不在本文件（缺陷 21 的教训）：定义在测试里，读
   `sqlite_store.py` 的人看不见「079 是被有意略过的」。本测试从执行器读那一份，**不自己
   维护副本** —— 豁免只有一个源，不存在两处要同步。

   **但这条锁只管「有没有归宿」，不管「归宿是否可接受」** —— 079 当时正是躺在豁免表里、
   本锁绿着、库依然缺表。豁免理由声明的后果由
   `test_sqlite_fresh_schema.py::test_fresh_db_covers_every_table_pg_declares` 去验。
2. `_ensure_initialized` 里不得有「失败只 print、从不重抛」的 except 块。此前 74 处
   `except Exception: print` 同时干了两件坏事：真失败被吞成「初始化成功」，
   而「已建库重跑」这条正常路径每次都打一行假失败（034）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from storage import sqlite_store

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "storage" / "migrations"
SQLITE_STORE = ROOT / "storage" / "sqlite_store.py"


def test_every_migration_file_is_dispatched():
    """目录里每个 .sql 都要有归宿 —— 接线了，或明确记下为什么不接。"""
    not_applied = sqlite_store._MIGRATIONS_NOT_APPLIED
    dispatched = (
        {"001_init.sql"}
        | set(sqlite_store._MIGRATIONS_BEFORE_USER_REBUILD)
        | set(sqlite_store._MIGRATIONS_AFTER_USER_REBUILD)
    )
    on_disk = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}

    missing = sorted(on_disk - dispatched - set(not_applied))
    assert not missing, (
        f"这些迁移文件躺在 storage/migrations/ 里却没人应用：{missing}。"
        "加进 sqlite_store 的次序表，或在 _MIGRATIONS_NOT_APPLIED 里写明理由。")
    unknown = sorted(dispatched - on_disk)
    assert not unknown, f"次序表指向了不存在的迁移文件：{unknown}"
    stale = sorted(set(not_applied) - on_disk)
    assert not stale, f"_MIGRATIONS_NOT_APPLIED 里的豁免指向已不存在的文件：{stale}"


def test_exemption_has_one_source_in_the_executor(monkeypatch):
    """证明豁免只有一个源：本测试读的是执行器的声明，不是自己那份副本。

    变异（即本用例）：往执行器的 `_MIGRATIONS_NOT_APPLIED` 塞一条指向不存在文件的假豁免，
    锁必须相应变红（stale 分支）。若测试另存副本，这条不会生效。
    """
    monkeypatch.setitem(sqlite_store._MIGRATIONS_NOT_APPLIED, "999_ghost.sql", "假豁免")
    with pytest.raises(AssertionError, match="已不存在的文件"):
        test_every_migration_file_is_dispatched()


def test_migration_runner_never_swallows_a_failure():
    """迁移执行区不得有「只 print、从不重抛」的 except 块。

    74 处同族块是靠人工逐个数出来的；这条锁让它不可能再长回来 —— 新写一个
    `except Exception: print(...)` 就红。
    """
    tree = ast.parse(SQLITE_STORE.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_ensure_initialized"),
        None,
    )
    assert fn is not None, "找不到 _ensure_initialized —— 锁的靶子没了，先修靶子"

    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not any(isinstance(x, ast.Raise) for x in ast.walk(handler)):
                bad.append(handler.lineno)
    assert not bad, (
        f"_ensure_initialized 第 {sorted(bad)} 行的 except 只 print、从不重抛 —— "
        "真失败会被吞成「初始化成功」。要么删掉这个 except（用确定性前置判断代替），"
        "要么在 handler 里 raise。")
