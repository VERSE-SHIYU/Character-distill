FROM openresty/openresty:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY waf.lua /etc/nginx/waf.lua
COPY cc.lua /etc/nginx/cc.lua

RUN mkdir -p /etc/ssl/certs /etc/ssl/private /var/log/openresty && \
    touch /var/log/openresty/waf.log /var/log/openresty/access.log /var/log/openresty/error.log

EXPOSE 80 443

CMD ["openresty", "-g", "daemon off;"]
