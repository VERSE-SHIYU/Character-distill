"""后端报错上报：sentry-sdk → 自托管 GlitchTip。

**`SENTRY_DSN` 为空 = 整个模块不生效。** 见 `init_error_reporting`：不 import SDK、
不初始化，行为与接线之前逐字一致 —— 所以本模块可以先合 main，不等 GlitchTip 就绪。

**为什么只从日志汇合点进。** 全仓约 300 处 `print + raise` 的未捕获异常都落在
`web/server.py` 的 `_global_exception_handler`（`logger.exception` 记一条带堆栈的
ERROR）。Sentry 的默认 `LoggingIntegration` 收这条，于是后端的错**全部**经这一处
汇合上报。`auto_enabling_integrations=False` 且不装 `[fastapi]` extra 是配套的：
放开自动集成或装上那个 extra，Starlette 集成会对同一个异常**再**报一条，同一个错误
两份事件。

**只报错，不采正文。** `traces_sample_rate=0`、`auto_session_tracking=False`；
`send_default_pii=False` 之上再加一道 `before_send`，把 `request.data` /
`request.cookies` 删掉 —— 本系统的请求体里是用户的对话与上传内容，不上报。

**`release` / `environment` 交给 SDK 自己读**（`SENTRY_RELEASE` /
`SENTRY_ENVIRONMENT`），不在这里转一手：部署侧下发这两个变量（`release` 就是
`APP_IMAGE_TAG`），SDK 的原生读法一处到位，多一层转发只会多一处能写错的地方。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: 环境变量名。**唯一读取点**在本模块。
SENTRY_DSN_ENV = "SENTRY_DSN"

#: 节点身份：与 `web/routers/auth.py` 注册 `home_region` 用的是同一个变量、同一个缺省。
#: 那边取的是函数内局部变量（`auth.py:436`），名字拿不到，只能按同一口径重读一遍。
NODE_REGION_ENV = "NODE_REGION"
DEFAULT_NODE_REGION = "cn-shenzhen"


def _scrub_request(event: dict, hint: dict) -> dict:
    """删掉请求正文与 cookie。上报只留「哪里错了」，不留用户内容。"""
    request = event.get("request")
    if request:
        request.pop("data", None)
        request.pop("cookies", None)
    return event


def init_error_reporting() -> None:
    """按 `SENTRY_DSN` 起不起上报。**在 lifespan 首行调用。**

    放首位不是因为它有什么前置依赖（它不发 LLM 调用），而是为了收到**启动期自身**
    抛出的错 —— 排后面的装配项一旦在启动时炸，这条出口还没接上。
    """
    dsn = os.environ.get(SENTRY_DSN_ENV, "").strip()
    if not dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        traces_sample_rate=0.0,
        auto_session_tracking=False,
        auto_enabling_integrations=False,
        before_send=_scrub_request,
        # 关掉堆栈帧的局部变量（SDK 缺省是开的）。上面那条 `before_send` 只删得掉
        # `request.data`，而请求正文还会从**另一条路**漏出去：出事的帧的局部变量里躺着
        # Starlette 的 `Request`（`body` / `body_bytes` / `json_body`）和 FastAPI 已解析
        # 好的入参。实测一条带 body 的未捕获路由异常，事件里原样带着
        # `"body": {"note": "..."}`。本系统的请求体就是用户的对话与上传内容。
        include_local_variables=False,
    )
    sentry_sdk.set_tag("region", os.environ.get(NODE_REGION_ENV, DEFAULT_NODE_REGION))
