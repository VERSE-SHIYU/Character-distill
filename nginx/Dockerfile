FROM openresty/openresty:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY waf.lua /etc/nginx/waf.lua
COPY cc.lua /etc/nginx/cc.lua
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN chmod +x /docker-entrypoint.sh && \
    mkdir -p /etc/ssl/certs /etc/ssl/private /var/log/openresty && \
    touch /var/log/openresty/waf.log /var/log/openresty/access.log /var/log/openresty/error.log

EXPOSE 80 443

ENTRYPOINT ["/docker-entrypoint.sh"]
