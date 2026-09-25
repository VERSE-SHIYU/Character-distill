# -*- coding: utf-8 -*-
"""前端上报配置的单一来源：`GET /api/client-config` 与 CSP 的 `connect-src`。

**为什么要有这个模块。** 前端的 DSN 有**两个**消费者：接口（前端取到后 `Sentry.init`）
和 CSP（`connect-src` 得放行这个 origin，否则浏览器把上报请求挡在门外 —— 前端手里有
DSN 也发不出去）。两处各读一次环境变量就会出现「接口给了新 DSN、CSP 还放着旧的」这种
半通状态：事件只在浏览器控制台里报一条 CSP 违规，服务端一条都收不到。故读取点收在本
模块，两边都从这里取。

**`SENTRY_FRONTEND_DSN` 与后端的 `SENTRY_DSN` 是两个键。** 后端那个不发给前端，前端用的
DSN 由 GlitchTip 里单独建的 project 给（见 `core/error_reporting.py` 的 `SENTRY_DSN`）。
这里只读前者。

**`release` / `region` 与后端同源**：`SENTRY_RELEASE` 由部署侧下发（后端也是 SDK 自己读
它），`region` 取 `core.node.node_region()` —— 前端事件因此能和后端事件对到同一个版本、
同一个节点上。
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter

from core.node import node_region

#: 环境变量名。**唯一读取点**在本模块。
SENTRY_FRONTEND_DSN_ENV = "SENTRY_FRONTEND_DSN"
SENTRY_RELEASE_ENV = "SENTRY_RELEASE"

#: `scheme://host[:port]` 的保守形状。DSN 由部署侧下发，而解析结果要拼进一条 HTTP 响应头
#: —— 形状不对（带逗号、分号、空白、换行之类）就整个拒掉，决不把没核过的东西写进
#: `connect-src`。顺带把 `urlsplit().port` 那条会抛 `ValueError` 的路也堵上了。
_ORIGIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[A-Za-z0-9.\-]+(?::\d{1,5})?\Z")


def sentry_frontend_dsn() -> str:
    """`SENTRY_FRONTEND_DSN` 的当前值（两侧空白去掉）。空串 = 未配置。"""
    return os.getenv(SENTRY_FRONTEND_DSN_ENV, "").strip()


def sentry_origin() -> str | None:
    """DSN 的 `scheme://host[:port]` —— 进 CSP `connect-src` 的那一项。

    未配置、或值解析不出一个像样的 origin 时返回 `None`：**拿不准就不加宽 CSP**，与
    「DSN 为空 = 上报整个不生效」同一口径。
    """
    parts = urlsplit(sentry_frontend_dsn())
    # DSN 的 netloc 是 `public_key@host[:port]`，取 @ 之后那截。
    candidate = f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"
    return candidate if _ORIGIN_RE.match(candidate) else None


router = APIRouter(prefix="/api/client-config", tags=["client-config"])


@router.get("")
def client_config() -> dict[str, str]:
    """前端启动时要的那几项。**未配置上报时返回 `{}`**。

    空对象就是「没有这份配置」这一个事实，与 `core/error_reporting.py` 那句「`SENTRY_DSN`
    为空 = 整个模块不生效」同一口径：前端照常渲染，只是不初始化 SDK。不填假 DSN，也不
    给 `enabled: false` 这类开关 —— 那是让前端去猜「拿到了但值是空的」算不算配置好了。

    `release` 只在部署侧真下发了才出现，同样不填空串：`SENTRY_RELEASE` 未设 = 没有版本
    可标（SDK 自己也是这么读的）。
    """
    dsn = sentry_frontend_dsn()
    if not dsn:
        return {}
    cfg = {"sentry_dsn": dsn, "region": node_region()}
    release = os.getenv(SENTRY_RELEASE_ENV, "").strip()
    if release:
        cfg["release"] = release
    return cfg


__all__ = [
    "SENTRY_FRONTEND_DSN_ENV", "SENTRY_RELEASE_ENV",
    "sentry_frontend_dsn", "sentry_origin", "router",
]
