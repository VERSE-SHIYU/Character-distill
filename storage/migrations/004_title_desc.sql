-- 此迁移仅对旧数据库有效（001_init.sql 已包含 title/description 列）。
-- 执行时会因列重复而忽略（try/catch），无需报错。
ALTER TABLE texts ADD COLUMN title TEXT DEFAULT '';
ALTER TABLE texts ADD COLUMN description TEXT DEFAULT '';
