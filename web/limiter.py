"""Shared rate limiter — used by server.py and route decorators."""

from slowapi import Limiter


def _get_real_ip(request) -> str:
    """CDN/代理回源时从header获取真实客户端IP，否则限流形同虚设。

    优先级：
    1. X-Real-IP（nginx 设为 $remote_addr，用户不可伪造）
    2. X-Forwarded-For 最后一个（nginx 用 $proxy_add_x_forwarded_for 在末尾追加真实 IP）
    3. 直连 IP
    """
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
