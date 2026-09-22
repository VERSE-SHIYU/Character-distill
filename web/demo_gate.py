# -*- coding: utf-8 -*-
"""演示账号门禁：演示账号**只能浏览与聊天**，其余写操作一律拒绝。

**事实与策略分离**，沿用 `core/request_context.py` / `web/llm_gate.py` 那条线：
「谁在调」是事实 —— 身份由 `web/server.py` 的 `AuthMiddleware` 解析一次、写在
`request.state.user` 上；本模块**只读它**，一行解析都不写。「许不许写」是策略 ——
全部落在本模块里，一处定义。

**为什么是全局依赖而不是中间件。** 中间件在**路由匹配之前**执行，那时还不知道
这条请求落到哪条路由上，只能按 URL 字符串猜，`{session_id}` 这类模板无从取得；
凡猜不到就只剩「路径前缀匹配」这种代理指标。全局依赖在**路由匹配之后**执行，
`request.scope["route"]` 就是匹配到的那条 `APIRoute`，`.path` 是**带前缀的完整
模板**（实测 `/api/history/{session_id}/resume`），判据因此是精确的。

**「是不是演示账号」不在这里定义。** 判据（用户的 role 列取值为 guest）全仓唯一定义在
`core/roles.py` 的 `roles.is_guest`，本模块只调用它。此前这里读一份环境变量名单，与
数据库里的身份是两套机制，同一个人可能在一边是访客、在另一边不是；名单已在身份收敛
为 `users.role` 单列时整条删除。

**默认拒绝 + 白名单。** 只读方法（GET/HEAD/OPTIONS）放行；写方法仅放行
`DEMO_WRITE_ALLOWLIST` 里逐条列出的 (方法, 路由模板)，其余一律 403。白名单是
**精确到模板**的，不是前缀 —— 前缀匹配会把 `/api/chat/send` 之外将来长出来的
`/api/chat/send_whatever` 一起放行，而新增写路由本该默认被拦。

**为什么「凭据在这里算不算数」是判据而不是名单。** 判据**不是**「路径在不在某个
公开名单上」（那是第二份要与 `PUBLIC_PATHS` 同步维护的清单，漏一条就是静默放行），
而是**这条路由的依赖树里有没有 `security_scheme`** —— 有，说明它自己会从凭据里解析
身份，凭据在这个端点上构成「以该账号行事」，本门才管得着（`/api/market/*` 的 20 条
写路由都是）；没有的（5 条公开鉴权路由 / 8 条 `/api/inter-node/*` 的 HMAC 机器接口），
凭据在这里不构成「以该账号行事」，本门不该管。

**这条判据为什么必须留**（去掉会让演示访客被锁死）：前端 `client.js` 的
`getAuthHeaders()` 只要 localStorage 里有 token 就挂 `Authorization`，于是访客带着
**demo 的 token** 打 `/api/auth/register`。中间件现在在公开路径上也会认出这个有效
凭据（「身份可选」，见 `web/server.py` 的 `PUBLIC_PATHS` 注释），于是
`request.state.user` 是**真的 demo 身份** —— 只按身份判就会回「演示账号不支持此操作，
注册后可使用完整功能」：文案让他去注册，行为不许他注册。故先按判据把这类路由整个
排除出射程：凭据在那儿不构成「以该账号行事」，本门无从判定，也不该拦。

**曾经的做法是「委派」**（`request.state.user` 为空且路由自鉴权时，调
`auth.get_optional_user` 再解析一次）。那是补中间件「公开路径一律置空身份」这个洞的，
代价是门禁得声明 `Depends(security_scheme/get_storage/get_jwt_secret)` —— 门禁是
**全局依赖**，这三个参数在**进函数体之前**被框架解析，于是每条请求（含公开 GET、
静态资源、`/`）都先把 secret 读一遍，`JWT_SECRET` 未配置时公开读变成 500。
根因修在中间件（公开路径改成「身份可选」），委派随之删除：本模块现在不声明任何
依赖参数，也不调用解析器。

**只读方法先短路**：GET 占绝大多数（静态资源、SPA、全部读接口），它们不该为一次
用不上的判定买单。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from core import roles
from routers.auth import security_scheme

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


def is_demo_write_allowed(method: str, path: str) -> bool:
    """演示账号用 *method* 打 *path* 这条路由模板，许不许。纯判定，不碰请求。"""
    method = method.upper()
    return method in READ_METHODS or (method, path) in DEMO_WRITE_ALLOWLIST


def _route_reads_credentials(route) -> bool:
    """凭据在这条路由上「算不算数」—— 它的依赖树里有没有 `security_scheme`。

    `get_current_user` / `get_optional_user` 都把 `security_scheme` 挂在自己的子依赖里，
    故查这一个符号就覆盖了「自行鉴权」的全部写法。递归而非只看第一层：将来多包一层
    `Depends(某个内部再 Depends(get_current_user))` 也认得出。

    判据取自**路由对象本身**（与 `route.path` 同一类事实），不是一份要与
    `PUBLIC_PATHS` 同步维护的路径名单 —— 名单漏一条就是静默放行。取不到 route 时
    恒为 False：拿不准就**不进门禁的射程**，与身份判定对空 user 的处置同一口径。
    """
    def _walk(dep) -> bool:
        for sub in getattr(dep, "dependencies", None) or ():
            if sub.call is security_scheme or _walk(sub):
                return True
        return False

    return _walk(getattr(route, "dependant", None))


async def demo_readonly_gate(request: Request) -> None:
    """门禁依赖本体。放行返回 None，拒绝抛 403 + `DEMO_REFUSAL`。

    顺序是刻意的，四步都别调换：

      1. **只读方法先放行** —— 读取一律不管。
      2. **凭据不算数、且路径本来就是公开的** → 整个排除。两个条件缺一不可：
         - 「公开」（`request.state.identity_optional`，中间件给的**事实**）：中间件
           本来就没打算在这儿拦失败，身份只是顺带认出来的。`/api/auth/register` 那 5 条
           就在这一类 —— 演示访客带着 demo 的 token 去注册，本门插进去只会把注册锁死
           （文案让他去注册，行为不许他注册）。
         - 「凭据不算数」（`_route_reads_credentials`）：这条路由自己不从凭据解析身份
           （5 条公开鉴权路由、8 条 `/api/inter-node/*` 的 HMAC 机器接口）。
         少了「公开」这一半，非公开路径上「没写鉴权依赖的新写路由」就会被整个放过 ——
         那正是「新增写接口默认被拦」这条性质的反面（负控用例 `_solo_app` 钉着它）。
         少了「凭据不算数」这一半，`/api/market/*` 的 20 条写路由会被漏掉。
      3. **判身份** —— 读 `AuthMiddleware` 已解析好的 `request.state.user`，交给
         `roles.is_guest`（定义在 `core/roles.py`）。
      4. **判目标** —— 写方法不在白名单即拒。

    取不到路由模板时 `path=""`，落不进白名单，于是**写方法 fail-closed**：拿不准就拒，
    不是放行。
    """
    if request.method.upper() in READ_METHODS:
        return
    route = request.scope.get("route")
    # 第 2 步：公开**且**凭据不算数 → 不在射程内（两条缺一不可，理由见文档串）。
    if getattr(request.state, "identity_optional", False) and not _route_reads_credentials(route):
        return
    if not roles.is_guest(getattr(request.state, "user", None) or {}):
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
    "DEMO_REFUSAL", "READ_METHODS", "DEMO_WRITE_ALLOWLIST",
    "is_demo_write_allowed", "demo_readonly_gate",
    "install_demo_gate",
]
