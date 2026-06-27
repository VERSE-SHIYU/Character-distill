#!/bin/sh
set -e

# ============================================================
# 跨境节点 IP 白名单注入
# 替换 nginx.conf 中的 PEER_NODE_IP_PLACEHOLDER 为实际对端 IP。
# 未设置 PEER_NODE_IP 时移除占位行（单节点部署兼容）。
# ============================================================
if [ -n "$PEER_NODE_IP" ]; then
    # 双节点：替换占位符为实际 IP（conf 行已有 allow 前缀）
    sed -i "s/PEER_NODE_IP_PLACEHOLDER/$PEER_NODE_IP/" /etc/nginx/nginx.conf
else
    # 单节点：没有对端，删除占位行（仅 127.0.0.1 允许，外部访问全 403）
    sed -i "/PEER_NODE_IP_PLACEHOLDER/d" /etc/nginx/nginx.conf
fi

# 启动前语法自检：allow allow <IP> 这类错误被 fail-fast 抓出
openresty -t -c /etc/nginx/nginx.conf || { echo "[entrypoint] nginx 配置校验失败，拒绝启动"; exit 1; }

# 预创建 WAF 日志文件，避免 fail2ban 因找不到 waf.log 而崩溃。
# waf.lua/cc.lua 仅在检测到攻击时写入此文件，无攻击时文件不会自动生成。
touch /var/log/openresty/waf.log

exec openresty -g "daemon off;" -c /etc/nginx/nginx.conf
