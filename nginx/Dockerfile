FROM openresty/openresty:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY waf.lua /etc/nginx/waf.lua
COPY cc.lua /etc/nginx/cc.lua

RUN mkdir -p /etc/ssl/certs /etc/ssl/private /var/log/openresty

EXPOSE 80 443

CMD ["openresty", "-g", "daemon off;"]
