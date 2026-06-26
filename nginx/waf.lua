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
    return ngx.exit(403)
end

-- 1. 拦截非标准请求方法
local method = ngx.req.get_method()
local allowed = { GET = true, POST = true, PUT = true, PATCH = true, DELETE = true, OPTIONS = true, HEAD = true }
if not allowed[method] then
    return deny("METHOD", method)
end

-- 2. SQL注入检测 (URI + query args)
local uri = ngx.var.uri:lower()
local args = (ngx.var.query_string or ""):lower()
-- 收紧规则：移除误杀风险高的 "select%s+.*from"（正常含 from 参数会被误封）
-- 和裸 "../"（已由下方独立的路径遍历规则更精确处理）
local sqli_patterns = {
    "union%s+select", "insert%s+into%s+", "drop%s+table%s+",
    "information_schema", "or%s+1%s*=%s*1", "'%s*or%s+'%s*=",
    "etc/passwd", "cmd%.exe", "/bin/bash",
}

-- 路径遍历：用更精确的模式（连续的 ../ 或编码形式），避免误杀正常路径
local traversal_patterns = { "%.%./%.%.", "%.%.%%2f", "%%2e%%2e/" }
for _, p in ipairs(traversal_patterns) do
    if ngx.re.find(uri, p, "jo") or ngx.re.find(args, p, "jo") then
        return deny("TRAVERSAL", ngx.var.request_uri)
    end
end
for _, p in ipairs(sqli_patterns) do
    if ngx.re.find(uri, p, "jo") or ngx.re.find(args, p, "jo") then
        return deny("SQLI", ngx.var.request_uri)
    end
end

-- 3. XSS检测 (script标签 / onerror等)
local xss_patterns = { "<script", "javascript:", "onerror=", "onload=", "<iframe", "<img%s+.*on" }
for _, p in ipairs(xss_patterns) do
    if ngx.re.find(args, p, "jo") then
        return deny("XSS", ngx.var.request_uri)
    end
end

-- 4. 恶意User-Agent拦截
local ua = ngx.var.http_user_agent or ""
local bad_ua = { "scrapy", "nikto", "sqlmap", "masscan", "nmap", "zgrab", "gobuster" }
for _, p in ipairs(bad_ua) do
    if ngx.re.find(ua:lower(), p, "jo") then
        return deny("BAD_UA", ua)
    end
end
