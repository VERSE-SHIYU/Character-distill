# SQLite 迁移（已冻结）

这批迁移**已冻结**：SQLite 自 2026-09-24 起仅作本地备用后端，不再测试、不再维护。
新迁移一律只写 `../migrations_pg/`。

**唯一例外**：PG 新增**列**时，在这里加一份同语义的孪生迁移（并登记进 `sqlite_store.py`
的迁移次序表），此外不加。冻结后第一次加列暴露了「SQLite 接口还能跑」与「不为它做迁移」
互斥 —— 新列没有那个列就谈不上接口能跑，而两侧**真库**的列集锁
（`tests/test_postgres_store.py::TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns`）
要求列集相等、且明文**不许开豁免**。列集锁是二者的仲裁：加列必须双写，其余一律不加。
