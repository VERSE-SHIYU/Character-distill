-- 092 — 退役 texts.content_resolved / coref_resolved：共指消解整条链路已删
--
-- 两列由 056 加、只服务 `Distiller.coref_resolve` 与 `DISTILL_USE_COREF` 开关；
-- 两者连同「按开关选原文/消解版」的读路径一并删除，字段再无写入方。
--
-- **幂等靠读现状，不靠 IF EXISTS**：SQLite 没有 `DROP COLUMN IF EXISTS`，
-- 执行器（sqlite_store.py 的 `_apply_migration`）对 DROP 支复用 ADD 支那套 PRAGMA
-- 读现状 —— 列已不在即视为「这句已生效」剥掉。故这里写的是裸 DROP。
--
-- **必须排在 056 之后**（056 在 BEFORE 段，本文件在 AFTER 段）：老库升级时 056 已空操作，
-- 但 056 的位次仍是「用户重建之前」，本文件的 DROP 要等 BEFORE 段整段跑完才轮到。
--
-- **本文件的 DROP 只对老库真正执行一次**：056 已空操作 ⇒ 新库从没建过这两列 ⇒
-- `_apply_migration` 的「整份跳过」条件（每句 DROP 的列都已不在）当场成立，重启不再
-- 重写整张 texts（存的是全文）。
--
-- 前提：`DROP COLUMN` 需要 SQLite ≥ 3.35。更老的版本会在启动时**响亮失败**
-- （语法错误上抛，执行器没有 except），不会静默留下列。

ALTER TABLE texts DROP COLUMN content_resolved;
ALTER TABLE texts DROP COLUMN coref_resolved;
