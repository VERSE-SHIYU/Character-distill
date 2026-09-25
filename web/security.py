"""Security hardening: response headers to prevent XSS, clickjacking, MIME sniffing."""

from starlette.middleware.base import BaseHTTPMiddleware

from web.client_config import sentry_origin


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(self), geolocation=()"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        # `connect-src` 放行前端上报的去处。取不到 origin（`SENTRY_FRONTEND_DSN` 未配置或
        # 不成形）就保持原值 —— 没接线时 CSP 与接线前逐字一致。来源与接口同一个
        # （`web/client_config.py`），否则会出现「接口给了 DSN、CSP 还不放行」的半通状态。
        connect_src = "connect-src 'self' https://api.deepseek.com"
        origin = sentry_origin()
        if origin:
            connect_src += f" {origin}"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            f"{connect_src}; "
            "media-src 'self' blob:; "
            "frame-ancestors 'none'"
        )
        return response
