# 基础镜像锁 digest：浮动 tag 会让上游一更新、代码没改的镜像也变，SZ 部署被迫跨境重下大层
#（2026-10-12 实发：openresty:alpine 上游变 → nginx 60MB 层重下 24 分钟）。升级走 Dependabot PR。
FROM openresty/openresty:alpine@sha256:2cb90d64192934c26b3749cf744f47edd59277787fe761bd109eeebdc3e2214a

COPY nginx.conf /etc/nginx/nginx.conf
COPY waf.lua /etc/nginx/waf.lua
COPY cc.lua /etc/nginx/cc.lua
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN chmod +x /docker-entrypoint.sh && \
    mkdir -p /etc/ssl/certs /etc/ssl/private /var/log/openresty && \
    touch /var/log/openresty/waf.log /var/log/openresty/access.log /var/log/openresty/error.log

EXPOSE 80 443

ENTRYPOINT ["/docker-entrypoint.sh"]
