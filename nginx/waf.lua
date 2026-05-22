-- WAF规则: SQL注入 / XSS / 路径遍历 / 恶意UA检测
-- OpenResty access_by_lua 阶段执行

local function log_block(rule, detail)
    local f = io.open("/var/log/openresty/waf.log", "a")
    if f then
        f:write(string.format("%s [%s] %s %s\n",
            ngx.var.time_iso8601, rule, ngx.var.remote_addr, detail))
        f:close()
    end
end

local function deny(rule, msg)
    log_block(rule, msg or "")
    ngx.status = 403
    ngx.say("Forbidden")
    ngx.exit(403)
end

-- 1. 拦截非标准请求方法
local method = ngx.req.get_method()
local allowed = { GET = true, POST = true, PATCH = true, DELETE = true, OPTIONS = true, HEAD = true }
if not allowed[method] then
    deny("METHOD", method)
end

-- 2. SQL注入检测 (URI + query args)
local uri = ngx.var.uri:lower()
local args = ngx.var.query_string:lower()
local sqli_patterns = {
    "union%s+select", "select%s+.*from", "insert%s+into", "drop%s+table",
    "exec%s+", "information_schema", "or%s+1%s*=%s*1", "'%s*or%s+'",
    "../", "etc/passwd", "cmd.exe", "/bin/bash",
}
for _, p in ipairs(sqli_patterns) do
    if ngx.re.find(uri, p, "jo") or ngx.re.find(args, p, "jo") then
        deny("SQLI", ngx.var.request_uri)
    end
end

-- 3. XSS检测 (script标签 / onerror等)
local xss_patterns = { "<script", "javascript:", "onerror=", "onload=", "<iframe", "<img%s+.*on" }
for _, p in ipairs(xss_patterns) do
    if ngx.re.find(args, p, "jo") then
        deny("XSS", ngx.var.request_uri)
    end
end

-- 4. 恶意User-Agent拦截
local ua = ngx.var.http_user_agent or ""
local bad_ua = { "scrapy", "nikto", "sqlmap", "masscan", "nmap", "zgrab", "gobuster" }
for _, p in ipairs(bad_ua) do
    if ngx.re.find(ua:lower(), p, "jo") then
        deny("BAD_UA", ua)
    end
end
