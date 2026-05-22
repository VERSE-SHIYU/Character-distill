-- CC防护: Lua精准限流（轻量，不需要外部模块）
-- 检测短时间内同IP大量请求，自动封禁

local cc_ban = ngx.shared.cc_ban
local cc_req = ngx.shared.cc_req

local ip = ngx.var.remote_addr
local now = ngx.time()

-- 检查是否已被封禁
local ban_until = cc_ban:get(ip)
if ban_until and tonumber(ban_until) > now then
    ngx.status = 429
    ngx.say("Too Many Requests")
    ngx.exit(429)
end

-- 10秒窗口计数
local window = math.floor(now / 10)
local key = ip .. ":" .. window
local count, _ = cc_req:incr(key, 1, 0)
if count == 1 then
    cc_req:expire(key, 20)
end

-- 阈值: 10秒内100次请求则封10分钟
if count > 100 then
    cc_ban:set(ip, now + 600, 600)
    ngx.status = 429
    ngx.say("Too Many Requests")
    ngx.exit(429)
end
