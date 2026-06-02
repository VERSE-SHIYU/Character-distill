"""Geo-based API guard: restrict LLM API usage based on client IP geolocation.

Compliance requirement:
  - Domestic IP (China) → only whitelisted domestic LLM base_urls allowed
  - International IP → no restriction
  - Cannot determine → fail closed (treated as domestic, blocked)

Uses ip2region offline database (via XdbSearchIP) for zero external lookup cost.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from typing import Tuple

logger = logging.getLogger("charsim.geo_guard")

# Whitelisted domestic LLM base URLs (host only, suffix-matched)
_DOMESTIC_LLM_HOSTS = [
    "api.deepseek.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",
    "dashscope.aliyuncs.com",
]

# Private IP ranges that should be treated as domestic (fail closed)
_PRIVATE_RANGES = [
    ("10.0.0.0", "10.255.255.255"),
    ("172.16.0.0", "172.31.255.255"),
    ("192.168.0.0", "192.168.255.255"),
    ("127.0.0.0", "127.255.255.255"),
]

# ── IP utilities ─────────────────────────────────────────────────────────────

_IP_OCTET_CACHE: dict[str, tuple[int, int, int, int]] = {}


def _ip_to_int(ip: str) -> int | None:
    """Convert dotted IPv4 to integer. Returns None on parse failure."""
    try:
        if ip not in _IP_OCTET_CACHE:
            _IP_OCTET_CACHE[ip] = tuple(int(o) for o in ip.strip().split("."))
        o = _IP_OCTET_CACHE[ip]
        return (o[0] << 24) + (o[1] << 16) + (o[2] << 8) + o[3]
    except (ValueError, IndexError, AttributeError):
        return None


def _is_in_any_range(ip_int: int, ranges: list[tuple[str, str]]) -> bool:
    for start_str, end_str in ranges:
        start = _ip_to_int(start_str)
        end = _ip_to_int(end_str)
        if start is not None and end is not None and start <= ip_int <= end:
            return True
    return False


def _is_private_ip(ip: str) -> bool:
    """Check if an IP is in private/reserved ranges."""
    ip_int = _ip_to_int(ip)
    if ip_int is None:
        return False
    return _is_in_any_range(ip_int, _PRIVATE_RANGES)


# ── XdbSearcher singleton ────────────────────────────────────────────────────

_searcher: object | None = None  # XdbSearcher instance (opaque type to avoid import)
_searcher_lock = threading.Lock()
_searcher_ok = False


def _get_searcher():
    """Lazy-init singleton XdbSearcher. Thread-safe."""
    global _searcher, _searcher_ok
    if _searcher_ok:
        return _searcher

    with _searcher_lock:
        if _searcher_ok:
            return _searcher
        try:
            import importlib.resources as _ir

            from XdbSearchIP.xdbSearcher import XdbSearcher

            dbfile = str(_ir.files("XdbSearchIP.data").joinpath("ip2region.xdb"))
            _searcher = XdbSearcher(dbfile=dbfile)
            _searcher_ok = True
            logger.info("[geo_guard] ip2region loaded from %s", dbfile)
        except Exception as exc:
            logger.warning("[geo_guard] Failed to load ip2region: %s", exc)
            _searcher = None
            _searcher_ok = False
        return _searcher


# ── Public API ───────────────────────────────────────────────────────────────


def is_domestic_ip(ip: str | None) -> bool:
    """Determine if an IP address is domestic (China).

    Returns True (domestic = restricted) when:
    - IP is None or empty
    - IP is private/reserved (10.x, 172.16-31.x, 192.168.x, 127.x)
    - ip2region lookup fails
    - ip2region returns China as country

    Fail-closed: any uncertainty → True.
    """
    if not ip or not ip.strip():
        return True  # empty → domestic (fail closed)

    ip = ip.strip()

    # Check private ranges first
    if _is_private_ip(ip):
        return True  # private IP → domestic (fail closed)

    searcher = _get_searcher()
    if searcher is None:
        return True  # ip2region not loaded → domestic (fail closed)

    try:
        result = searcher.search(ip)
        if not result:
            return True  # lookup returned empty → domestic (fail closed)
        country = result.split("|")[0]
        if not country or country == "0":
            return True  # lookup failed → domestic (fail closed)
        return country == "中国"
    except Exception:
        logger.warning("[geo_guard] ip2region lookup failed for %s", ip, exc_info=True)
        return True  # exception → domestic (fail closed)


def is_whitelisted_base_url(base_url: str) -> bool:
    """Check if a base_url's host is in the domestic LLM whitelist.

    Uses suffix matching to prevent host injection attacks:
      - exact match: api.deepseek.com == api.deepseek.com → True
      - suffix match: host.endswith(".api.deepseek.com") → True
      - injection:   api.deepseek.com.evil.com → False (doesn't end with .api.deepseek.com)
    """
    if not base_url:
        return False
    try:
        parsed = urllib.parse.urlparse(base_url)
        host = parsed.hostname
        if not host:
            return False
        host = host.lower()
        for allowed in _DOMESTIC_LLM_HOSTS:
            if host == allowed or host.endswith("." + allowed):
                return True
        return False
    except Exception:
        return False  # parse failure → not whitelisted (fail closed)


def check_api_allowed(client_ip: str | None, base_url: str) -> Tuple[bool, str]:
    """Combined geo + whitelist check.

    Returns:
        (True, "") if allowed.
        (False, reason) if blocked — reason is a user-facing Chinese message.
    """
    if not base_url:
        return False, "API 地址不能为空"

    # Step 1: is the base_url in the domestic whitelist?
    if is_whitelisted_base_url(base_url):
        return True, ""

    # Step 2: not whitelisted — check if the client is domestic
    if is_domestic_ip(client_ip):
        return (
            False,
            "当前网络环境（中国大陆）暂不支持境外模型，"
            "请使用 DeepSeek / Kimi / 智谱 / 通义",
        )

    # International IP, non-whitelisted URL → allowed
    return True, ""
