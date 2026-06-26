#!/bin/sh
set -e

# ============================================================
# 跨境节点 IP 白名单注入
# 替换 nginx.conf 中的 PEER_NODE_IP_PLACEHOLDER 为实际对端 IP。
# 未设置 PEER_NODE_IP 时移除占位行（单节点部署兼容）。
# ============================================================
if [ -n "$PEER_NODE_IP" ]; then
    # 双节点：将对端 IP 注入 allow 指令
    sed -i "s/PEER_NODE_IP_PLACEHOLDER/allow $PEER_NODE_IP;  # 对端节点公网IP/" /etc/nginx/nginx.conf
else
    # 单节点：没有对端，删除占位行（仅 127.0.0.1 允许，外部访问全 403）
    sed -i "/PEER_NODE_IP_PLACEHOLDER/d" /etc/nginx/nginx.conf
fi

exec openresty -g "daemon off;"
