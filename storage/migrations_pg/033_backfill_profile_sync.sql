-- 033: 存量用户资料补发一次（跨境 outbox，op_type = 'user_profile'）。
--
-- 注册同步缺陷（注册路由把函数 `node_region` 当地区传给发送函数，序列化抛错被吞）期间
-- 注册的用户，资料从没发到对端。这里把**本库全部用户**各入队一条，由补发循环发送：
-- 发送时读最新资料，且只发 home_region 等于本节点地区的用户（本库不知道自己是哪个区，
-- 过滤在发送方 `cross_border_sync.forward_user_profile_to_peer`）。
-- 对端 `/api/inter-node/user/sync` 是 upsert，已同步过的用户再发一次无副作用。
-- 'user_profile' 与 `storage.base.USER_PROFILE_OP` 同值；payload 是版本戳，见该常量注释。
-- 已有待发行保持原样（DO NOTHING）：它本身就会发最新资料。
--
-- 编号排在 031（表改名）之后：原先是 030、写旧表名。账本为空时全部迁移会重放，009 会把旧表
-- 重建成空壳，写在改名之前就会往空壳里塞行，031 见到非空的旧表会拒绝继续。
INSERT INTO cross_border_outbox (op_type, target_id, payload)
SELECT 'user_profile', id, gen_random_uuid()::text
FROM users
ON CONFLICT (op_type, target_id) DO NOTHING;
