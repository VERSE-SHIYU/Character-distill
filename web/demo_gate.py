# -*- coding: utf-8 -*-
"""演示账号门禁：演示账号**只能浏览与聊天**，其余写操作一律拒绝。

**事实与策略分离**，沿用 `core/request_context.py` / `web/llm_gate.py` 那条线：
「谁在调」是事实 —— 身份由 `web/server.py` 的 `AuthMiddleware` 解析一次、写在
`request.state.user` 上；本模块**不复写解析逻辑**，只在中间件没解析的那类路径上
向既有解析器要一次（见下）。「许不许写」是策略 —— 全部落在本模块里，一处定义。

**为什么是全局依赖而不是中间件。** 中间件在**路由匹配之前**执行，那时还不知道
这条请求落到哪条路由上，只能按 URL 字符串猜，`{session_id}` 这类模板无从取得；
凡猜不到就只剩「路径前缀匹配」这种代理指标。全局依赖在**路由匹配之后**执行，
`request.scope["route"]` 就是匹配到的那条 `APIRoute`，`.path` 是**带前缀的完整
模板**（实测 `/api/history/{session_id}/resume`），判据因此是精确的。

**为什么「是不是演示账号」只在本模块定义一次。** 身份解析目前有 3 处重复
（`AuthMiddleware` / `auth.get_current_user` / `auth.get_optional_user`）。把
`is_demo` 的结果挂到 user 字典上，就得同时改那 3 处，漏一处就是静默放行或静默拒绝。
所以口径是 `is_demo(user)` 这个**纯函数**，谁拿到 user 字典谁自己调。
（3 处解析的收敛方案见本次工作记录的报告，属另一笔。）

**默认拒绝 + 白名单。** 只读方法（GET/HEAD/OPTIONS）放行；写方法仅放行
`DEMO_WRITE_ALLOWLIST` 里逐条列出的 (方法, 路由模板)，其余一律 403。白名单是
**精确到模板**的，不是前缀 —— 前缀匹配会把 `/api/chat/send` 之外将来长出来的
`/api/chat/send_whatever` 一起放行，而新增写路由本该默认被拦。

**公开前缀路径上的身份（这条不补，门禁会漏掉 20 条写路由）。** `AuthMiddleware`
对公开路径把 `request.state.user` 置为 `{}` —— 它的语义是「这里不 401，路由自己
会鉴权」，**不是**「这条请求没有身份」。而 `PUBLIC_PREFIXES` 里就有 `/api/market/`，
于是 `/api/market/*` 的写方法（发帖、评论、点赞、fork、发布、关注、删除…）带着
**有效 token** 走到这里时，`request.state.user` 是 `{}`；只看它就会判成「非演示账号」
放行，那 20 条写路由也就全部漏掉（实测：`POST /api/market/{id}/like` 返回的是端点自己的
404，不是本门的 403）。所以：**写方法、`request.state.user` 为空、且这条路由自己
会解析凭据时**，委派给 `auth.get_optional_user` 再解析一次。这是委派、不是第二份
口径 —— 解析规则仍只有 `routers/auth.py` 那一处；本模块只多知道「去哪儿问」。

**为什么委派要加「且这条路由自己会解析凭据」这半句。** 无差别委派会把
`/api/auth/register` 一起拦掉，而那正是拒绝文案让演示访客去做的事 —— 前端
`client.js` 的 `getAuthHeaders()` 只要 localStorage 里有 token 就挂 `Authorization`，
于是访客带着 **demo 的 token** 去注册，委派解析出 demo 身份、`/api/auth/register`
又不在白名单，得到 403「注册后可使用完整功能」：文案与行为自相矛盾，访客被锁死在
演示账号里。判据**不是**「路径在不在某个公开名单上」（那是第二份要与 `PUBLIC_PATHS`
同步维护的清单），而是**这条路由的依赖树里有没有 `security_scheme`** —— 有，说明它
自己会从凭据里解析身份（`/api/market/*` 的 20 条写路由都是），委派才有意义；没有
（5 条公开鉴权路由 / 8 条 `/api/inter-node/*` 的 HMAC 机器接口），凭据在这个端点上
不构成「以该账号行事」，门禁不该管。

**只读方法先短路**，连这次委派都不走：GET 占绝大多数（静态资源、SPA、全部读接口），
它们不该为一次用不上的身份解析买单。于是委派的判定只落在「公开前缀路径上的写方法」
（`/api/market/*` 20 条 + `/api/inter-node/*` 8 条），而非演示用户走的非公开写路径
读的是中间件已解析好的值，零额外开销。

**真正不经过演示判定的写方法**只有公开路径上那 5 条鉴权路由
（`login` / `register` / `refresh` / `reset-password` / `send-code`）—— 它们是
`PUBLIC_PATHS` 且**不带** `/api/market/` 那种「路由自鉴权」前缀，中间件不解析、
`get_optional_user` 也拿不到凭据，故恒判为非演示账号。这是对的：演示访客必须能注册
一个真账号（提示文案让他去注册），也必须能刷新 token 才留得住。故它们**不进白名单**
—— 进了也只是够不着的死条目。`/api/inter-node/*` 同理（HMAC 机器接口，不带用户身份）。
"""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from deps import get_storage
from routers.auth import get_jwt_secret, get_optional_user, security_scheme

#: 环境变量名。**全仓唯一读取点**在本模块（`demo_usernames`），测试有锁钉住这点。
DEMO_USERNAMES_ENV = "DEMO_USERNAMES"

#: 被拒时的提示文案。前端 `fetchWithTimeout` 取 `body.detail` 直接展示，
#: 所以这句就是用户看到的东西，与产品口径一致地写「注册后可使用完整功能」。
DEMO_REFUSAL = "演示账号不支持此操作，注册后可使用完整功能"

#: 只读方法：不查白名单直接放行。
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: 演示账号**唯一**可用的写操作，按「方法 + 路由模板」精确匹配。
#:
#: 前四条是「浏览 + 聊天」的骨架：开卡建会话、续聊历史会话、发消息、登出。
#:
#: 后两条是查证过**不产生越权写**才进来的，不是顺手放的：
#:   - `/api/text/{text_id}/progress`：翻页时的阅读进度，前端 300ms 防抖 + 静默
#:     `.catch()`。它写的是**自己**的进度行，且翻书属「浏览」本身。
#:   - `/api/voice/synthesize`：TTS 只读计算，不调 LLM（Edge TTS 免费 / 自托管
#:     GPT-SoVITS），已有 20/minute 限流 + 200 字上限，产物不落库。
#:
#: 刻意**不在**表里：`/api/chat/reset`（清空共享对话）、`/api/chat/revoke`、
#: `/api/chat/message/{message_id}/react` —— 这三个改的是预置对话本身，演示账号
#: 改一次，下一个访客看到的就不是预置内容了。
DEMO_WRITE_ALLOWLIST = frozenset({
    ("POST", "/api/chat/send"),
    ("POST", "/api/distill/start_session"),
    ("POST", "/api/history/{session_id}/resume"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/text/{text_id}/progress"),
    ("POST", "/api/voice/synthesize"),
})


def demo_usernames() -> frozenset[str]:
    """`DEMO_USERNAMES` 的当前值 —— 逗号分隔，逐项 strip + 小写。

    **不缓存**：与 `ADMIN_INVITE_CODE` 同形（用时就地读 env）。改配置重启即生效，
    不用为此维护失效逻辑；每请求一次 `os.getenv` 的成本可以忽略。
    """
    raw = os.getenv(DEMO_USERNAMES_ENV, "")
    return frozenset(name.strip().lower() for name in raw.split(",") if name.strip())


def is_demo(user: dict | None) -> bool:
    """这个身份是不是演示账号。纯函数，判据只看 `username`。

    大小写不敏感，与 `users.username_lower` 那一列同口径 —— 否则 `Demo` 与 `demo`
    会是两个身份，写进 env 的大小写决定门禁管不管得住。

    空 user（未登录 / 公开路径）恒为 False：门禁不是鉴权，没登录的人不由它管。
    """
    username = ((user or {}).get("username") or "").strip().lower()
    return bool(username) and username in demo_usernames()


def is_demo_write_allowed(method: str, path: str) -> bool:
    """演示账号用 *method* 打 *path* 这条路由模板，许不许。纯判定，不碰请求。"""
    method = method.upper()
    return method in READ_METHODS or (method, path) in DEMO_WRITE_ALLOWLIST


def _route_reads_credentials(route) -> bool:
    """这条路由自己会从凭据里解析身份吗 —— 依赖树里有没有 `security_scheme`。

    `get_current_user` / `get_optional_user` 都把 `security_scheme` 挂在自己的子依赖里，
    故查这一个符号就覆盖了「自行鉴权」的全部写法。递归而非只看第一层：将来多包一层
    `Depends(某个内部再 Depends(get_current_user))` 也认得出。

    判据取自**路由对象本身**（与 `route.path` 同一类事实），不是一份要与
    `PUBLIC_PATHS` 同步维护的路径名单 —— 名单漏一条就是静默放行。取不到 route 时
    恒为 False：拿不准就不委派，与 `is_demo` 对空 user 的处置同一口径。
    """
    def _walk(dep) -> bool:
        for sub in getattr(dep, "dependencies", None) or ():
            if sub.call is security_scheme or _walk(sub):
                return True
        return False

    return _walk(getattr(route, "dependant", None))


async def demo_readonly_gate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    storage=Depends(get_storage),
    secret: str = Depends(get_jwt_secret),
) -> None:
    """门禁依赖本体。放行返回 None，拒绝抛 403 + `DEMO_REFUSAL`。

    顺序是刻意的，三步都别调换：

      1. **只读方法先放行** —— 连身份都不解析（委派只发生在写方法上，见模块文档）。
      2. **拿身份** —— 优先用 `AuthMiddleware` 已解析的 `request.state.user`；为空
         （= 公开路径，典型是 `/api/market/*`）**且这条路由自己会解析凭据**时，
         才委派 `get_optional_user`。后半个条件见模块文档「为什么委派要加这半句」。
      3. **判目标** —— 读 `request.scope["route"]`，写方法不在白名单即拒。

    取不到路由模板时 `path=""`，落不进白名单，于是**写方法 fail-closed**：拿不准就拒，
    不是放行。
    """
    if request.method.upper() in READ_METHODS:
        return
    route = request.scope.get("route")
    user = getattr(request.state, "user", None) or {}
    if not user and _route_reads_credentials(route):
        user = await get_optional_user(credentials, storage, secret) or {}
    if not is_demo(user):
        return
    if is_demo_write_allowed(request.method, getattr(route, "path", "")):
        return
    raise HTTPException(403, DEMO_REFUSAL)


def install_demo_gate(app) -> None:
    """装配入口：把门禁挂成 app 的全局依赖。生产（`web/server.py`）与测试共用这一个。

    **必须在任何路由登记之前调用**（`include_router` / `@app.get` …），不能在
    `_lifespan` 里装。框架在**路由创建时**把 `router.dependencies` 快照进那条路由的
    dependant 里（`APIRouter.add_api_route` 的 `current_dependencies = self.dependencies.copy()`，
    以及 `include_router` 时 `_RouterIncludeContext.for_include` 的那个 `[*parent.dependencies, ...]`），
    路由登记之后再往这里 append 是**静默 no-op** —— 不报错、门禁就是不生效。

    与 `install_llm_gate` 的装配位置不同，是**被框架逼的**，不是风格分歧：那扇门改的是
    适配器的进程级守卫，本门挂的是这一个 app 的依赖表，故写在 app 构造之后。
    """
    app.router.dependencies.append(Depends(demo_readonly_gate))


__all__ = [
    "DEMO_USERNAMES_ENV", "DEMO_REFUSAL", "READ_METHODS", "DEMO_WRITE_ALLOWLIST",
    "demo_usernames", "is_demo", "is_demo_write_allowed", "demo_readonly_gate",
    "install_demo_gate",
]
