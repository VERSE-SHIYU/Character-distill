"""FastAPI entry point: create app, mount routers, serve static files."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

# 在所有会触发模型加载的 import 之前，先执行全局 meta-tensor 防御。
# 此模块设置环境变量、torch 默认设备，并修补 nn.Module.to。
import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_WEB_DIR = Path(__file__).resolve().parent
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

import yaml
from pydantic import BaseModel

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from limiter import get_client_ip, limiter
from core.clock import is_valid_timezone, set_current_timezone
from core.request_context import Caller, LLM_CALLER

from security import SecurityHeadersMiddleware

from routers.text import router as text_router
from routers.distill import legacy_router as distill_legacy
from routers.distill import router as distill_router
from routers.chat import legacy_router as chat_legacy
from routers.chat import router as chat_router
from routers.history import router as history_router
from routers.voice import router as voice_router
from routers.wechat import router as wechat_router
from routers.card import router as card_router
from routers.market import router as market_router
from routers.group import router as group_router
from routers.message import router as message_router
from routers.inter_node import router as inter_node_router
from routers.memory import router as memory_router
from routers.auth import get_current_user, router as auth_router
from routers.auth import (
    IDENTITY_REJECTIONS,
    Verdict,
    get_jwt_secret,
    resolve_request_identity,
    validate_fernet_key,
    validate_jwt_secret,
)
from inter_node_auth import validate_inter_node_secret
from routers.admin import require_admin, router as admin_router
from cross_border_sync import _cross_border_resync_loop
from deps import get_config, get_llm, get_storage, reset_llm_and_dependents, _session_cleanup_loop
from adapters.llm_adapter import llm_error_payload, llm_error_types, user_facing_error
from web.llm_gate import install_llm_gate
from web.demo_gate import install_demo_gate
from storage.base import StorageBase
from core.log_collector import install_log_collector
from core.alerting import install_alert_handler

logger = logging.getLogger(__name__)

_FRONTEND_DIST_DIR = _WEB_DIR / "frontend" / "dist"
_LEGACY_STATIC_DIR = _WEB_DIR / "static"
if _FRONTEND_DIST_DIR.exists():
    _STATIC_DIR = _FRONTEND_DIST_DIR
else:
    _STATIC_DIR = _LEGACY_STATIC_DIR
    print(
        "[server] frontend dist not found at web/frontend/dist. "
        "Run `cd web/frontend && npm run build` for production files, "
        "or `npm run dev` for Vite dev server."
    )

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 门在**启动动作的最前面**：守卫是进程级全局，装配期注册（与 `set_main_loop`
    # 同一处形态）。放第一位是因为启动过程本身若出现 LLM 调用，门必须在那一刻已成立。
    #
    # **不放 import 期**：`import web.server` 改变进程级策略，会让同一进程里任何
    # 不启 app 的代码（适配器单测、脚本）凭空被门管住 —— 序依赖随之而来（谁先 import
    # 决定谁被拦）。注册是**装配**这件事的一部分，就写在装配处。
    install_llm_gate(app)
    validate_fernet_key()
    validate_jwt_secret()
    validate_inter_node_secret()
    install_log_collector()
    # 与面板同一个装配处：面板负责「留在进程里等人来查」，告警负责「推出去」。
    # `ALERT_EMAIL` 未配置时不安装，只记一条 WARNING（见 core/alerting）。
    install_alert_handler()
    loop = asyncio.get_running_loop()
    # context 传播点注记：`asyncio.to_thread` 会拷贝 contextvar（标准库内部走
    # `contextvars.copy_context()`），裸 `loop.run_in_executor` **不会** —— 后者只把
    # 裸函数丢进执行器，不碰 context。故「执行器本身不是断点」这句话对前者成立、
    # 对后者是错的（实测 Python 3.12，读数与依据见 tests/census_llm_call_contexts.py §一）。
    # 生产代码零处裸用 run_in_executor，故这里是注记订正，不是需要包装的缺陷；
    # 将来若要在执行器里跑需要上下文的活，走 `asyncio.to_thread` 或 core.concurrency。
    loop.set_default_executor(ThreadPoolExecutor(max_workers=200, thread_name_prefix="chat_pool"))
    from deps import set_main_loop
    set_main_loop(loop)
    await _preload_embedding()
    await _reconcile_distill_tasks()
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    resync_task = asyncio.create_task(_cross_border_resync_loop())
    yield
    # 顺序：先停清理循环并**等它退出**，再补写队列。反过来的话两边会同时 flush 同一把
    # 队列 —— 清理循环可能正把会话出队，而这里正拿着它补写；也会让「谁在写」这件事
    # 在关停期间变得说不清。
    cleanup_task.cancel()
    resync_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await resync_task
    except asyncio.CancelledError:
        pass
    # 正常关停不该丢消息：队列在内存里，进程一走就没了。异常退出（崩溃）丢队列是已知
    # 边界，台账 94 写了。
    from deps import flush_outboxes, get_group_sessions, get_sessions
    try:
        flushed = await flush_outboxes(get_sessions()) + await flush_outboxes(get_group_sessions())
        if flushed:
            print(f"[shutdown] flushed {flushed} queued message(s)")
    except Exception as exc:
        logger.error("flush outboxes failed (non-fatal): %s", exc, exc_info=True)


app = FastAPI(title="Character Simulator API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)

app.state.limiter = limiter

# 演示账号门禁在**任何路由登记之前**装：框架在路由创建时就快照 router.dependencies，
# 路由登记之后再装是静默 no-op（故不能像 install_llm_gate 那样放 `_lifespan`）。
# 装配位置与那条硬约束的完整说明见 `web/demo_gate.py`。
install_demo_gate(app)


async def _preload_embedding():
    """Embedding is now API-based (DashScope), no local model to preload."""
    print("[startup] Embedding: DashScope API mode (on-demand, no preload needed)")


async def _reconcile_distill_tasks():
    """开机仲裁：把所有 status='running' 的蒸馏任务置 interrupted。

    前提是「单 region 单进程模型」—— 进程刚起、内存 worker 集为空，running 行只能
    来自已死的上一个进程。若将来多开进程/加 worker，新启动进程会把另一个进程正在跑
    的任务误标 interrupted —— 那时必须改成基于 heartbeat / lease 的认领机制再移除本
    函数。interrupted 是步骤 3 断点续跑挑选恢复对象的直接依据。
    """
    try:
        n = await get_storage().mark_interrupted_distills()
        if n:
            print(f"[distill] Boot reconcile: {n} orphan running task(s) → interrupted")
    except Exception as exc:
        # 启动期 DB 未就绪等不可致命：任务以 running 留存，下次启动再仲裁
        logger.warning("Boot reconcile failed (non-fatal): %s", exc, exc_info=True)


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---- 领域异常 → HTTP 状态码：唯一定义处 ----
# 路由层只 raise 领域异常，不碰文案、不碰状态码。上屏文案一律取 ``user_facing_error``
# （adapters 里的唯一出口）—— **不在此新建第二张消息表**，这里只管「码」。
#
# 每行的依据是**现状归纳**，不是重新设计（AGENTS.md 缺陷 38）：
#   DistillError → 400   原先 /run 的 `except ValueError` 就是 400（DistillError 是 ValueError 子类）
#   StoreError   → **不在表内**：storage/base.py 的类注释已把「记 traceback 并回 500」指派给
#                  下面的全局处理器。在此注册会**丢掉 traceback** —— 那是降级，不是统一。
#   未登记异常     → 500   原有兜底，不动
#
# 流式（SSE）与后台任务两条链**不走这里**：异常发生时响应头已在线（HTTP 200 +
# event-stream）或根本没有响应，处理器产出的是第二个响应、Starlette 不会再发 ——
# 管不到，不是不让管。那两条继续用 `user_facing_error` 取文案，共用同一份口径链。
from core.distiller import DistillError  # noqa: E402  此处引入，避开文件顶部 meta-tensor 防御块

_DOMAIN_ERROR_STATUS: dict[type, int] = {DistillError: 400}

# LLM 侧已知失败 → 状态码：**一张表**，按 `llm_error_payload` 的判别键 `kind` 查。
# 未完成终态：同一异常类下 finish_reason 语义不同（content_filter 是用户可修正的输入
# 问题 400、length 是上游截断 502、资源不足可稍后重试 503）；调用点门拒绝是 403
# （D1：被 geo 拦截统一 403，不回落全局 key）。未登记兜 502（上游问题，不按我们的故障）。
# 这张表原先长在 web/routers/chat.py，且只认 finish_reason —— **搬家 + 收拢成一张**，
# 不是再造第二张让配码变成「先查 A 表再查 B 表」。
_LLM_ERROR_STATUS: dict[str, int] = {
    "incomplete:content_filter": 400,
    "incomplete:length": 502,
    "incomplete:insufficient_system_resource": 503,
    "call_refused": 403,
}
_LLM_ERROR_STATUS_DEFAULT = 502


def _domain_error_status(exc: Exception) -> int:
    """按 MRO 判码 —— 注册按基类做，子类也要命中（Starlette 的分发本身就是按 MRO 走的）。"""
    for cls, status in _DOMAIN_ERROR_STATUS.items():
        if isinstance(exc, cls):
            return status
    return 500


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=_domain_error_status(exc),
        content={"detail": user_facing_error(exc)},
    )


def _llm_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """LLM 侧已知异常的统一出口：**只配码 + 上屏文案**。

    审计**不在出口**：出口不唯一 —— 除了这条统一出口，其余宽 `except` 路径上的拒绝
    （chat 的兜底、SSE 错误帧、蒸馏的 `user_facing_error`）一条都不经过这里，挂在这
    等于「有的拒绝记、有的不记」。审计挂在**门**那一侧（`geo_call_guard` 拿到理由就
    记），那里是每个拒绝都必经之处。

    **同步**是刻意的（`_domain_error_handler` 保持 async 也各有各的理）：本函数一个
    await 也没有，只组装一个响应 —— 没必要占一个事件循环任务。
    """
    payload = llm_error_payload(exc) or {}
    kind = payload.get("kind", "")
    status = _LLM_ERROR_STATUS.get(kind, _LLM_ERROR_STATUS_DEFAULT)
    return JSONResponse(status_code=status, content={"detail": user_facing_error(exc)})


def register_domain_error_handlers(target_app: FastAPI) -> None:
    """把领域异常挂到统一出口。抽成函数是为了让测试能在**自己的最小 app** 上装同一份注册 ——
    测试与生产共用这一处，才拦得住「注册表被改空而测试没动」的变异。"""
    for _cls in _DOMAIN_ERROR_STATUS:
        target_app.add_exception_handler(_cls, _domain_error_handler)
    # LLM 侧异常类由 adapters 发布（此处不 import 类名 —— 边界锁禁 core/web/storage 出现该标识）
    for _cls in llm_error_types():
        target_app.add_exception_handler(_cls, _llm_error_handler)


register_domain_error_handlers(app)
# 调用点门的注册见 `_lifespan` 首行 —— 装配期的事写在装配处，不在 import 期发生。


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 往上抛的错误统一在这里落一条带堆栈的 ERROR：约 300 处 `print + raise` 会走到这儿，
    # 它们本身不改成日志（错误已由此处记录），但在此之前它们只落在 stdout 里 —— 面板
    # （RingBufferHandler，收 WARNING+）和告警邮件（收 ERROR）都看不见。
    logger.exception("%s %s 未捕获的异常", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )

_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if not _origins_env:
    print(
        "\n[WARN] ALLOWED_ORIGINS 未设置！CORS 将拒绝所有跨域请求。"
        "\n   请在 .env 中设置: ALLOWED_ORIGINS=https://你的域名\n"
    )
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["http://localhost:5173"]

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Time-Zone"],
)

# ---- Auth middleware ----
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import time

#: 「身份**可选**」的路径：中间件照常解析身份，只是**失败不拦**。
#:
#: 语义不是「这里没有身份」——那会让带凭据的请求在这里被当成匿名，`/api/market/*` 的
#: 写路由与 `request.state.user` 的读者都会拿到空身份。带有效凭据时身份就是真的；
#: 没带、过期、无效，才退化成空身份放行。
PUBLIC_PATHS = {"/api/auth/register", "/api/auth/login", "/api/auth/refresh", "/api/auth/send-code", "/api/auth/reset-password", "/api/health", "/api/health/ready", "/api/announcement/active"}
PUBLIC_PREFIXES = ("/assets/", "/static/", "/favicon", "/manifest", "/login", "/api/market/", "/api/inter-node/")

# Throttle last_active updates to once per 60s per user
_last_active_ticks: dict[str, float] = {}


def _maybe_update_last_active(user_id: str) -> None:
    now = time.time()
    last = _last_active_ticks.get(user_id, 0)
    if now - last < 60:
        return
    _last_active_ticks[user_id] = now
    import asyncio
    asyncio.ensure_future(get_storage().update_last_active(user_id))


def _valid_tz(tz: object) -> str:
    """头 / 库里的时区值：能被 `ZoneInfo` 解析才算数，否则当没有（空串）。

    头里可能是伪造或旧客户端写的垃圾，库里可能是手改脏数据 —— 两处都得判一次，
    不能拿「反正 `UserClock` 会兜底」当理由：兜底的是**用**，写库还得先判该不该写。
    """
    return tz if isinstance(tz, str) and is_valid_timezone(tz) else ""


def _maybe_update_timezone(user_id: str, tz: str) -> None:
    """把这次请求头里的时区落库 —— 与 `_maybe_update_last_active` 同一形态的顺手更新。

    不需要节流表：写一次之后库值就等于头值了，下一次请求的 `header_tz != stored_tz`
    自然不成立。只有用户真换了时区才会再写一次。
    """
    asyncio.ensure_future(get_storage().update_user_timezone(user_id, tz))


class AuthMiddleware(BaseHTTPMiddleware):
    """鉴权 + **一次**身份解析 + **一次**身份上下文的设置。

    三个「一次」都是判据，不是风格：

    **解析只有一处**（`resolve_request_identity` 在 `web/server.py` 里就这一行）。公开路径
    与非公开路径的差别**只是失败拦不拦** —— 若按路径分叉成两段解析，两段会各自漂移
    而互不报错。带 Bearer 凭据就解析一次，判出来是什么就是什么。

    **这一处必须是复用入口**（`resolve_request_identity`，不是 `resolve_identity`）：
    端点侧的 `get_current_user` / `get_optional_user` 是同一个请求的第二次调用，走复用
    入口才拿得到这里算好的那份，否则同一次请求要判两遍、多查一次库。

    **出口只有一个**：`call_next` 之前设身份 contextvar（`LLM_CALLER` —— 门的策略与
    记账的归属读的是同一个）。早退的 401/403 各自 return 响应（没有下游，不需要
    上下文）；公开路径与鉴权路径在出口处合流 —— 「身份可选」的那条也把认出来的身份
    带走，只是判失败时退化成空身份而不是拦下。

    设在出口**之前**是硬要求：contextvar 只对 `call_next` 之后的下游可见，设在它
    后面，端点体与流式响应体读到的都是默认值（实测，见 L8）。

    **没凭据时不碰 secret、不碰 storage**：secret 以取值函数传入（未配置时取值会抛），
    storage 的取值在「有 Bearer 凭据」的分支里 —— 于是一条没带凭据的公开请求既不
    因为一个与本请求无关的配置变成 500，也不做无谓的库读。
    """

    async def dispatch(self, request: Request, call_next):
        user: dict = {}
        user_id: str | None = None
        path = request.url.path
        # 「身份可选」：公开路径照常解析，只是判失败时不拦（语义见 PUBLIC_PATHS 注释）。
        public = (path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
                  or not path.startswith("/api/"))

        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        verdict: Verdict
        if token and scheme.lower() == "bearer":
            # secret 传**函数本身**，不在这里取值：取值会抛（JWT_SECRET 未配置时），
            # 而一条没带凭据的请求本该 401、不该因为配置变成 500。按需取值见
            # `resolve_identity`；这里走复用入口，端点侧的适配器才拿得到这份判定。
            verdict, user = await resolve_request_identity(
                request, token, get_jwt_secret, get_storage(),
            )
        else:
            verdict, user = Verdict.MISSING, None

        if verdict is not Verdict.OK:
            if not public:
                status, detail = IDENTITY_REJECTIONS[verdict]
                return JSONResponse({"detail": detail}, status_code=status)
            user = {}
        else:
            user_id = user["id"]
            # 公开路径不记活跃：`/api/market/*` 是轮询路径，写放大换不到东西。
            if not public:
                _maybe_update_last_active(user_id)

        request.state.user = user
        # 公开路径的要判失败不拦这件事，下游（演示门禁）需要知道：它决定「凭据在不在
        # 这条路由上算数」时要用到这个事实，而 `PUBLIC_PATHS` 只在本模块定义一次。
        request.state.identity_optional = public
        # 身份只设这一处：门的策略（geo）与记账的归属读的都是它（缺陷 35）
        LLM_CALLER.set(Caller(ip=get_client_ip(request), user_id=user_id))
        # 时区也在这里**统一确定**（缺陷 96）：可解析的 `Time-Zone` 头 → 用户已存时区 →
        # 不设（`UserClock` 回退 DEFAULT_TZ）。业务侧从此只认 `UserClock`，不再各处传时区。
        # 无条件设一次而不是「有值才设」：contextvar 不设就可能留下上一个请求的值。
        header_tz = _valid_tz(request.headers.get("Time-Zone"))
        stored_tz = _valid_tz(user.get("timezone")) if user else ""
        if header_tz and not public and user_id and header_tz != stored_tz:
            _maybe_update_timezone(user_id, header_tz)
        set_current_timezone(header_tz or stored_tz)
        return await call_next(request)


# ---- Auth middleware (last added = outermost, before include_router for exception_handler) ----
app.add_middleware(AuthMiddleware)

# ---- Mount routers ----
app.include_router(auth_router)      # 不需要认证
app.include_router(admin_router)
app.include_router(text_router)
app.include_router(distill_router)
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(voice_router)
app.include_router(wechat_router)
app.include_router(card_router)
app.include_router(market_router)
app.include_router(group_router)
app.include_router(message_router)
app.include_router(inter_node_router)
app.include_router(memory_router)


# ---- Public: announce router ----
_announce_router = APIRouter(prefix="/api/announcement", tags=["announce"])


@_announce_router.get("/active")
async def public_active_announcement(
    storage: StorageBase = Depends(get_storage),
):
    """Get the currently active announcement (no auth required)."""
    return await storage.get_active_announcement() or {}


app.include_router(_announce_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """只看**存活**：进程起来了、能应答。刻意不碰库。

    与 `/api/health/ready` 的分工不可合并：容器存活与库可用是两件事。合成一条会让
    「app 没起来」与「app 起来了但库连不上」共用一个红信号 —— 缺陷 41 那个形态，
    也正是部署脚本要分两段门的原因。
    """
    return {"status": "ok"}


@app.get("/api/health/ready")
async def health_ready(
    storage: StorageBase = Depends(get_storage),
) -> JSONResponse:
    """就绪：进程在跑 **且** 存储答得上话（真查一次库）。

    响应体只带状态词。异常类型与文本只进日志 —— 那条路径上的异常可能带着 DSN、主机名、
    库名、用户名，而本路径在 `PUBLIC_PATHS` 里（无 token 可访问），谁都能取。

    **已知取舍**：库**连构造都失败**（`get_storage()` 自己抛）发生在 `Depends` 阶段，
    那时还没进函数体，于是返回 500 而不是上面那个 503。不修：部署门对 500 一样红，
    响应体是框架默认文案，不比 503 多漏任何东西。区分「构造失败」与「ping 失败」不值得
    为此把存储构造搬进函数体 —— 那会把单例缓存和依赖注入一起绕掉。
    """
    try:
        await storage.ping()
    except Exception as exc:
        logger.warning("[health] readiness probe failed: %s: %s", type(exc).__name__, exc)
        return JSONResponse({"status": "unready"}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.get("/api/settings/config")
def read_settings_config(
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Read LLM + voice config for settings UI (authenticated users)."""
    try:
        llm = get_config().get("llm", {})
        voice = get_config().get("voice", {})
        has_key = bool(llm.get("api_key") or os.getenv("DEEPSEEK_API_KEY"))
        return {
            "base_url": str(llm.get("base_url", "")),
            "model": str(llm.get("model", "")),
            "api_key": "***" if has_key else "",
            "summary_threshold": int(llm.get("summary_threshold", 50)),
            "gptsovits_url": str(voice.get("gptsovits_url", "http://127.0.0.1:9880")),
            "funasr_url": str(voice.get("funasr_url", "ws://127.0.0.1:10095")),
        }
    except Exception as exc:
        print(f"[server] Read settings config failed: {exc}")
        raise HTTPException(500, "Read config failed") from exc


class UpdateConfigRequest(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    summary_threshold: int | None = None
    gptsovits_url: str | None = None
    funasr_url: str | None = None


@app.post("/api/settings/config")
@limiter.limit("30/minute")
async def update_settings_config(
    request: Request,
    req: UpdateConfigRequest,
    admin_user: dict = Depends(require_admin),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """Update LLM + voice config at runtime and persist to config.yaml."""
    try:
        cfg = get_config()
        llm = cfg.setdefault("llm", {})
        changes = []

        def _changed(field: str, old: Any, new: Any) -> bool:
            return new is not None and str(new).strip() and str(new).strip() != str(old).strip()

        for field, val in [("base_url", req.base_url), ("model", req.model), ("api_key", req.api_key), ("summary_threshold", req.summary_threshold)]:
            old = llm.get(field, "")
            if val is not None and (not isinstance(val, str) or val.strip()):
                new_val = val.strip() if isinstance(val, str) else val
                if str(new_val) != str(old):
                    changes.append((field, str(old), str(new_val)))
                    llm[field] = new_val

        voice = cfg.setdefault("voice", {})
        for field, val in [("gptsovits_url", req.gptsovits_url), ("funasr_url", req.funasr_url)]:
            old = voice.get(field, "")
            if val is not None and val.strip() and val.strip() != str(old).strip():
                changes.append((field, str(old), val.strip()))
                voice[field] = val.strip()

        cfg_path = _REPO_ROOT / "config.yaml"
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

        # Log config changes —— 审计写入失败不得让「已落盘」的配置保存回 500：
        # config.yaml 在上面已经写完，回错会让人以为没保存。容忍策略就地写，
        # 不藏回 store（store 现在对库失败一律上抛）。
        if changes:
            import uuid
            for field, old_val, new_val in changes:
                try:
                    await storage.save_config_change(
                        uuid.uuid4().hex[:12], admin_user["id"], admin_user.get("username", ""),
                        field, old_val, new_val,
                    )
                except Exception as exc:
                    logger.error("Save config change failed (non-fatal): %s", exc, exc_info=True)

        # 先持久化到 config.yaml，再调用 reset_llm_and_dependents()
        reset_llm_and_dependents()

        return {
            "base_url": str(llm.get("base_url", "")),
            "model": str(llm.get("model", "")),
            "api_key": "***" if llm.get("api_key") else "",
            "summary_threshold": int(llm.get("summary_threshold", 50)),
            "gptsovits_url": str(voice.get("gptsovits_url", "http://127.0.0.1:9880")),
            "funasr_url": str(voice.get("funasr_url", "ws://127.0.0.1:10095")),
            # 保存**不拦**：全局 LLM 置 None 是 C4 已定的口径，管理员可能就是有意清空。
            # 但调用方得知道这件事发生了 —— 没有个人 key 的用户此后一律 503。
            "llm_available": get_llm() is not None,
        }
    except Exception as exc:
        print(f"[server] Update config failed: {exc}")
        # 上屏不带 `{exc}`：与上面的 read 分支同口径（缺陷 38 同形态）。
        raise HTTPException(500, "Update config failed") from exc


@app.post("/api/settings/test-gptsovits")
async def test_gptsovits_connection(req: Request, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """Test GPT-SoVITS connectivity."""
    try:
        body = await req.json()
        url = body.get("url", "").strip()
        if not url:
            url = get_config().get("voice", {}).get("gptsovits_url", "http://127.0.0.1:9880")
        from speech.voice_clone import VoiceCloneClient
        vc = VoiceCloneClient(base_url=url)
        ok = await vc.health_check()
        return {"ok": ok, "url": url}
    except Exception as exc:
        logger.warning("Test GPT-SoVITS failed: %s", exc, exc_info=True)
        return {"ok": False, "url": "", "error": str(exc)}


@app.post("/api/settings/test-funasr")
async def test_funasr_connection(req: Request, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """Test FunASR connectivity."""
    try:
        body = await req.json()
        url = body.get("url", "").strip()
        if not url:
            url = get_config().get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")
        from speech.funasr_client import FunASRClient
        client = FunASRClient(url=url)
        ok = await client.is_available()
        return {"ok": ok, "url": url}
    except Exception as exc:
        logger.warning("Test FunASR failed: %s", exc, exc_info=True)
        return {"ok": False, "url": "", "error": str(exc)}


# Legacy compat: keep old /api/identify, /api/distill, /api/chat, /api/reset
app.include_router(distill_legacy)
app.include_router(chat_legacy)


# ---- Static files ----
# Vite build references /assets/* and /favicon.svg at site root (not under /static).


@app.get("/")
def serve_index():
    """Serve the frontend index page."""
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "index.html not found. Build frontend with "
                "`cd web/frontend && npm run build` or run `npm run dev`."
            ),
        )
    return FileResponse(index_path)


@app.get("/favicon.svg")
def serve_favicon() -> FileResponse:
    """Vite public favicon."""
    path = _STATIC_DIR / "favicon.svg"
    if not path.exists():
        raise HTTPException(404, "favicon.svg not found")
    return FileResponse(path)


@app.get("/icons.svg")
def serve_icons() -> FileResponse:
    """Sprite sheet from Vite public/."""
    path = _STATIC_DIR / "icons.svg"
    if not path.exists():
        raise HTTPException(404, "icons.svg not found")
    return FileResponse(path)


# ---- Static file mounts (must be registered BEFORE the catch-all) ----
_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_assets_dir)),
        name="frontend_assets",
    )

if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    print(f"[server] WARNING: Static dir {_STATIC_DIR} not found — serving API only.")

# Voice cache audio files
_voice_cache_dir = _REPO_ROOT / "data" / "voice_cache"
_voice_cache_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_voice_cache_dir)), name="voice_audio")


@app.get("/{path:path}")
def serve_spa(path: str):
    """SPA fallback: serve index.html for all non-API routes (must be last-registered route)."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    # Serve real static files at the root level (e.g. Chinese-named files)
    # before falling back to index.html.
    candidate = _STATIC_DIR / path
    try:
        real = os.path.realpath(candidate)
    except (OSError, ValueError):
        real = None
    if real is not None and os.path.isfile(real):
        try:
            Path(real).resolve().relative_to(_STATIC_DIR.resolve())
            return FileResponse(real)
        except ValueError:
            pass  # path traversal attempt — fall through to index.html
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn
    print("Server starting: http://localhost:7860")
    uvicorn.run(app, host="0.0.0.0", port=7860)
