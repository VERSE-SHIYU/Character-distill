-- CC防护: Lua精准限流（轻量，不需要外部模块）
-- 检测短时间内同IP大量请求，自动封禁

local cc_ban = ngx.shared.cc_ban
local cc_req = ngx.shared.cc_req

local ip = ngx.var.remote_addr
local now = ngx.time()

-- 检查是否已被封禁
local ban_until = cc_ban:get(ip)
if ban_until and tonumber(ban_until) > now then
    ngx.header.content_type = "application/json"
    ngx.status = 429
    ngx.say('{"detail":"请求过于频繁，请稍后再试"}')
    ngx.exit(429)
end

-- 10秒窗口计数
local window = math.floor(now / 10)
local key = ip .. ":" .. window
local count, _ = cc_req:incr(key, 1, 0)
if count == 1 then
    cc_req:expire(key, 20)
end

-- 阈值: 10秒内300次请求则封60秒（SPA 页面含大量静态/API请求，100次容易误伤）
if count > 300 then
    cc_ban:set(ip, now + 60, 60)
    -- 写入日志供Fail2Ban检测
    local f = io.open("/var/log/openresty/waf.log", "a")
    if f then
        f:write(string.format("%s [CC] %s request_count=%d\n",
            ngx.var.time_iso8601, ip, count))
        f:close()
    end
    ngx.header.content_type = "application/json"
    ngx.status = 429
    ngx.say('{"detail":"请求过于频繁，请稍后再试"}')
    ngx.exit(429)
end
