"""Shared rate limiter — used by server.py and route decorators."""

from slowapi import Limiter


def _get_real_ip(request) -> str:
    """CDN/代理回源时从header获取真实客户端IP，否则限流形同虚设."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
