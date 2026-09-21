# -*- coding: utf-8 -*-
"""演示账号门禁（`web/demo_gate.py`）的判据。

**被测对象是生产 app 本身**（`server.app`），不是另搭一个最小 app。门禁挂在
`app.router.dependencies` 上，而那份依赖表是在**路由登记时**被快照进每条路由的
（`install_demo_gate` 的文档串）。所以只有走真 app，「生产这一份装配真的生效了」
才是被观测到的事实 —— 另搭一个 app 只是把 `install_demo_gate` 再调一次，与生产
是不是同一个装配无关。

**判据只有两种取值**：到没到端点。到了（任何状态码，包括端点自己的 404/422）算放行；
没到（403 + `DEMO_REFUSAL` 那行文案）算拦下。所以下面没有一条用例依赖端点的业务
语义 —— 门禁的射程就是「到没到」，测它的业务返回是越界。同理，白名单里的
`/api/voice/synthesize` 只喂空 body：喂真文本会真去调 TTS，而本文件不测 TTS。

**边界（写清楚免得被当成漏洞）**：不锁 `is_demo` 有没有被挂在 user 字典上（那是
实现），不锁门禁用了哪种解析器（那是 `routers/auth.py` 的账），也不测并发/时序。

Run: pytest tests/test_demo_gate.py -v
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import route_facts
import server
from routers.auth import JWT_ALGORITHM, _create_access_token, get_jwt_secret, security_scheme
from server import AuthMiddleware
from storage.sqlite_store import SQLiteStore
from web.demo_gate import (
    DEMO_REFUSAL,
    DEMO_USERNAMES_ENV,
    DEMO_WRITE_ALLOWLIST,
    READ_METHODS,
    demo_usernames,
    install_demo_gate,
    is_demo,
    is_demo_write_allowed,
)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 扫描面里逐条排除的目录：`tests/` 是锁自己的家（判据从被测对象现算，不扫自己），
#: `scripts/` 是运维/调试脚本（独立进程，不装门）。
_NON_PROD_TOP = ("tests/", "scripts/")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


#: 所有测试账号共用一份口令散列。argon2 每次 `recommended()` + `hash()` 约 0.35s，
#: 逐账号算一遍要在这个文件里白烧掉十几秒。这些是临时库里的丢弃账号，共用散列不削弱
#: 任何判据 —— 唯一验口令的那条（改密码）只要求它能被验出来。
_PW_HASH = PasswordHash.recommended().hash("Pass1234")


def _mk_user(store, username: str) -> dict:
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    return _run(store.create_user(uid, username, _PW_HASH))


def _headers(user: dict) -> dict:
    tok = _create_access_token(user["id"], user["username"], get_jwt_secret())
    return {"Authorization": f"Bearer {tok}"}


def _url(path: str) -> str:
    """路由模板 → 一个具体 URL（占位符一律填 "probe"）。"""
    return re.sub(r"\{[^}]+\}", "probe", path)


def _expired_token() -> str:
    """签名**有效**、但 `exp` 已过去的 token —— 与「随机串」是两种事态。"""
    return jwt.encode(
        {"sub": "usr_whoever", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        get_jwt_secret(), algorithm=JWT_ALGORITHM,
    )


def _gate_blocked(r) -> bool:
    """这次响应是不是**本门**给的。

    只看状态码不够：端点自己也会 403（非管理员、账号禁用、审核待审…），把那些算成
    「拦下了」会让门禁没生效时这条判据照样绿。文案是本门唯一的签名。
    """
    if r.status_code != 403:
        return False
    try:
        return r.json().get("detail") == DEMO_REFUSAL
    except ValueError:
        return False


# ── 夹具 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "demo_gate.db"))


@pytest.fixture
def demo_username():
    """**混合大小写**建号，env 里写小写 —— 顺带把「大小写不敏感」钉在真实链路上。

    `is_demo` 的大小写口径不是装饰：写进 env 的大小写若决定门禁管不管得住，那就是
    同一个账号有两个身份。
    """
    return "OfferPass_" + uuid.uuid4().hex[:6]


@pytest.fixture
def accounts(store, demo_username, monkeypatch):
    monkeypatch.setenv(DEMO_USERNAMES_ENV, demo_username.lower())
    return _mk_user(store, demo_username), _mk_user(store, "Plain_" + uuid.uuid4().hex[:6])


@pytest.fixture
def demo(accounts):
    return accounts[0]


@pytest.fixture
def plain(accounts):
    return accounts[1]


@pytest.fixture
def app(store, accounts, monkeypatch):
    """**生产 app**，只把 storage 换成临时库。

    换 `deps._storage` 一处就够：`server` / `web/demo_gate` / 全部路由拿到的
    `get_storage` 都是 `deps.get_storage` 同一个函数对象，读的是同一个 `_storage`。
    """
    monkeypatch.setattr(deps, "_storage", store)
    return server.app


@pytest.fixture
def client(app):
    """`raise_server_exceptions=False`：扫描要的是**响应**，不是异常。

    端点内部炸了会得到 500，那时扫描该报「没拦住」而不是把整条用例掀掉。
    """
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 纯函数层：身份判定与白名单判定
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsDemo:
    def test_unset_env_makes_nobody_a_demo(self, monkeypatch):
        monkeypatch.delenv(DEMO_USERNAMES_ENV, raising=False)
        assert demo_usernames() == frozenset()
        assert is_demo({"username": "anyone"}) is False

    def test_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv(DEMO_USERNAMES_ENV, " Aa , bb ,, ")
        assert demo_usernames() == frozenset({"aa", "bb"})
        assert is_demo({"username": "Aa"}) is True
        assert is_demo({"username": "  aa  "}) is True
        assert is_demo({"username": "cc"}) is False

    def test_empty_identity_is_never_a_demo(self, monkeypatch):
        """空 user 恒判 False —— **门禁不是鉴权**，没登录的人不由它管（那是 401 的账）。

        这条是「公开路径放行」这个前提本身：`AuthMiddleware` 对公开路径把身份置空，
        而公开读接口与 5 条公开鉴权路由都不该被本门碰。空 user 若判 True（如恒真的
        「无身份也算演示」写法），上面那批就全被拦死。
        """
        monkeypatch.setenv(DEMO_USERNAMES_ENV, "demo")
        for empty in (None, {}, {"username": ""}, {"username": "   "}, {"username": None}):
            assert is_demo(empty) is False, f"{empty!r} 被判成了演示账号"


class TestIsDemoWriteAllowed:
    def test_read_methods_pass_without_lookup(self):
        for m in READ_METHODS:
            assert is_demo_write_allowed(m, "/api/cards/whatever") is True

    def test_allowlist_entries_pass(self):
        for method, path in DEMO_WRITE_ALLOWLIST:
            assert is_demo_write_allowed(method, path) is True

    def test_match_is_exact_not_prefix(self):
        """白名单是**精确到模板**的。

        前缀匹配会把将来长出来的 `/api/chat/send_whatever` 一起放行，而新增写路由
        本该默认被拦 —— 这个差别只能用「多一个后缀」来钉，逐条列出白名单做不到。
        """
        assert is_demo_write_allowed("POST", "/api/chat/send") is True
        assert is_demo_write_allowed("POST", "/api/chat/send/") is False
        assert is_demo_write_allowed("POST", "/api/chat/send_whatever") is False
        assert is_demo_write_allowed("POST", "/api/chat") is False
        # 取不到路由模板时 `path=""`，落不进白名单 → 写方法 fail-closed
        assert is_demo_write_allowed("POST", "") is False

    def test_allowlist_has_no_read_entries_and_methods_are_upper(self):
        """白名单里不该有只读方法 —— 读方法本来就放行，列进来是个够不着的死条目，
        而它读起来像「这条要特别允许」，会把人带偏。"""
        for method, path in DEMO_WRITE_ALLOWLIST:
            assert method not in READ_METHODS, f"{method} {path} 是死条目"
            assert method == method.upper(), f"{method} {path} 的方法没大写"
            assert path.startswith("/"), f"{path} 不是完整路由模板"
        assert DEMO_WRITE_ALLOWLIST, "白名单为空 —— 下面几条会恒真"


# ═══════════════════════════════════════════════════════════════════════════════
# 行为层：生产 app + 生产中间件
# ═══════════════════════════════════════════════════════════════════════════════

class TestDemoAccountBehaviour:
    def test_reads_pass(self, client, demo):
        r = client.get("/api/history/list", headers=_headers(demo))
        assert r.status_code == 200, r.text

    def test_allowlisted_writes_reach_their_endpoints(self, client, demo):
        """6 条白名单写操作都要**够得着端点**，且给出的必须是端点自己的答复。

        够得着的强证据是端点自己的码，且这两个都**与本机环境无关**：`/api/chat/send`
        喂空 body 得 422（框架的请求校验跑到了，说明请求已过门），`/api/auth/logout`
        无 body 依赖得 200。刻意不喂真会话/真文本 —— 那两条会去碰 `get_user_llm`
        （无 API key 得 503）与 TTS，于是判据变成「这台机器恰好配了什么」，不是门禁。
        另外四条同样只断言「不是本门的 403」：它们的成功路径各要真资源/真外部服务，
        而本门只负责「到没到」。
        """
        h = _headers(demo)
        r = client.post("/api/chat/send", headers=h, json={})
        assert r.status_code == 422, f"chat/send 没走到端点：{r.status_code} {r.text}"

        r = client.post("/api/auth/logout", headers=h)
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text

        for method, path in DEMO_WRITE_ALLOWLIST:
            if path in ("/api/chat/send", "/api/auth/logout"):
                continue
            r = client.request(method, _url(path), headers=h, json={})
            assert not _gate_blocked(r), f"{method} {path} 被本门拦了：{r.status_code} {r.text}"

    @pytest.mark.parametrize("method,path", [
        ("PUT", "/api/auth/password"),            # 改密码（改了就把演示账号锁死）
        ("PATCH", "/api/auth/api-config"),        # 改自己的 API 配置
        ("POST", "/api/settings/config"),         # 改全局 API 配置
        ("POST", "/api/chat/reset"),              # 清空预置对话本身
        ("POST", "/api/chat/revoke"),
        ("POST", "/api/chat/message/1/react"),
        ("POST", "/api/market/author/posts"),     # 发帖
        ("POST", "/api/market/probe/like"),       # 点赞（公开路径，靠路由自鉴权）
        ("DELETE", "/api/market/probe"),
        ("POST", "/api/messages/send"),           # 私信
        ("DELETE", "/api/cards/probe"),
        ("DELETE", "/api/history/probe"),
    ])
    def test_denied_writes_are_403_with_the_message(self, client, demo, method, path):
        r = client.request(method, path, headers=_headers(demo), json={})
        assert r.status_code == 403, f"{method} {path} 没被拦：{r.status_code} {r.text}"
        assert r.json().get("detail") == DEMO_REFUSAL, r.text

    def test_denied_write_really_did_not_happen(self, client, demo, store):
        """403 之外再钉一次**副作用没发生**。

        只断言状态码的话，「先写了再返回 403」这种实现会照样绿。挑删卡这条：卡是真
        建的、属主是演示账号自己 —— 门禁若不在，它会 200 且卡就没了。
        """
        tid = f"txt_{uuid.uuid4().hex}"
        _run(store.save_text(tid, "src.txt", "正文", user_id=demo["id"]))
        cid = f"card_{uuid.uuid4().hex}"
        _run(store.save_card(cid, tid, "张三", '{"name":"张三"}', user_id=demo["id"]))

        r = client.delete(f"/api/cards/{cid}", headers=_headers(demo))
        assert r.status_code == 403, r.text
        assert _run(store.get_card_owned(cid, demo["id"])) is not None, "卡被删掉了"

    def test_public_auth_writes_still_reachable_while_holding_the_demo_token(
            self, client, demo):
        """**委派必须不覆盖这 5 条**（本文件最容易被改坏的一条）。

        前端 `client.js` 的 `getAuthHeaders()` 只要 localStorage 里有 token 就挂
        `Authorization`，于是演示访客点「注册」时是**带着 demo 的 token** 打
        `/api/auth/register` 的。门禁若在这里委派解析出 demo 身份，就会回
        「演示账号不支持此操作，注册后可使用完整功能」—— 文案让他去注册，行为不许他
        注册。所以这批不白名单、而是**根本不进门的射程**，判据是它们带着 demo 凭据
        也要走到端点自己的答复（缺 body → 422）。
        """
        h = _headers(demo)
        for method, path, body in [
            ("POST", "/api/auth/register", {}),
            ("POST", "/api/auth/login", {}),
            ("POST", "/api/auth/refresh", {}),
            ("POST", "/api/auth/send-code", {}),
            ("POST", "/api/auth/reset-password", {}),
        ]:
            r = client.request(method, path, headers=h, json=body)
            assert not _gate_blocked(r), f"{method} {path} 被本门拦了：{r.status_code} {r.text}"
            assert r.status_code == 422, f"{method} {path} 没到端点：{r.status_code} {r.text}"


class TestNonDemoAccountUnchanged:
    """非演示账号：同一批写操作照旧。门禁是**只对表内账号生效**的，不能顺手加严。"""

    def test_password_change_reaches_the_endpoint(self, client, plain):
        r = client.put("/api/auth/password", headers=_headers(plain),
                       json={"old_password": "wrong", "new_password": "NewPass1234"})
        assert r.status_code == 400, f"没走到端点自己的校验：{r.status_code} {r.text}"
        assert "当前密码错误" in r.text

    def test_card_delete_succeeds(self, client, plain, store):
        """删卡这条不光要看状态码，还要看**状态真的变了**。

        判据用「再删一次得 404」（软删的实现里，已在回收站的记录与不存在同判 404）——
        不去读行本身：`DELETE /api/cards/{id}` 是**软删**（行留着、`deleted_at` 落上），
        拿行还在不在当判据会红，而那是实现细节不是门禁的事。
        """
        tid = f"txt_{uuid.uuid4().hex}"
        _run(store.save_text(tid, "src.txt", "正文", user_id=plain["id"]))
        cid = f"card_{uuid.uuid4().hex}"
        _run(store.save_card(cid, tid, "张三", '{"name":"张三"}', user_id=plain["id"]))

        r = client.delete(f"/api/cards/{cid}", headers=_headers(plain))
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text

        r = client.delete(f"/api/cards/{cid}", headers=_headers(plain))
        assert r.status_code == 404, f"卡没被删掉（再删一次还是 {r.status_code}）：{r.text}"

    def test_chat_reset_really_executes(self, client, plain):
        """非演示账号的 reset 要**真跑到**引擎上。

        内存里塞一个会话让 `_ensure_session` 走内存命中分支 —— 否则它会去重建引擎、
        碰到 `get_user_llm`，于是本条的成败变成「这台机器恰好配没配 API key」。
        读 `resets` 而不是只看 200：200 也可能是「先 200 再什么都没做」。
        """
        from deps import get_sessions

        class _Engine:
            def __init__(self):
                self.resets = 0

            def reset(self):
                self.resets += 1

        sid = f"ses_{uuid.uuid4().hex}"
        session = {"engine": _Engine(), "user_id": plain["id"]}
        get_sessions()[sid] = session
        try:
            r = client.post("/api/chat/reset", headers=_headers(plain), json={"session_id": sid})
        finally:
            get_sessions().pop(sid, None)

        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        assert session["engine"].resets == 1, "reset 没真的执行"

    def test_same_requests_are_not_gate_blocked(self, client, plain):
        """逐条：非演示账号打这些写路由，得到的都不是本门的 403。"""
        for method, path in [
            ("POST", "/api/chat/revoke"),
            ("PATCH", "/api/auth/api-config"), ("POST", "/api/settings/config"),
            ("POST", "/api/market/author/posts"), ("DELETE", "/api/market/probe"),
        ]:
            r = client.request(method, path, headers=_headers(plain), json={})
            assert not _gate_blocked(r), f"{method} {path} 对非演示账号也拦了：{r.text}"


class TestIdentityIsOptionalNotIgnored:
    """公开路径上「有凭据就认身份」—— 曾经是「一律置空」。

    这两件事是一体两面，少任何一面都出问题：一律置空 ⇒ 20 条 `/api/market/*` 写路由
    漏在门禁射程外（于是有了那次「委派」补丁，而委派要求门禁自己声明
    `Depends(security_scheme/get_storage/get_jwt_secret)` —— 门禁是**全局依赖**，
    于是每条请求、包括公开 GET，都先把 secret 读一遍）；一律拦截 ⇒ 公开页面对
    过期凭据回 401，而公开的语义是「这里不鉴权」，不是「这里必须没有身份」。
    """

    def test_public_reads_survive_an_unset_secret(self, client, monkeypatch):
        """`JWT_SECRET` 未配置时，公开**读**路径不该因为一个与本请求无关的配置变成 500。

        判据是具体状态码而不是「!= 500」：`!= 500` 对「整条路由没了、回 404」也是绿的，
        而那正是另一种坏法。唯一放宽的是 `/`（前端产物不在仓里，404 与 200 都算够到
        路由，故只否掉 500）。

        **不含** `/api/auth/register` 这类要**签发** token 的写路由：它们真需要 secret，
        未配置时 500 是对的（那是另一件事，不是「公开读被配置拖累」）。
        """
        monkeypatch.delenv("JWT_SECRET", raising=False)
        for url in ("/api/health", "/api/announcement/active", "/api/market/tags"):
            r = client.get(url)
            assert r.status_code == 200, (
                f"GET {url} 在 JWT_SECRET 未配置时返回 {r.status_code}，期望 200：{r.text[:200]}"
            )
        assert client.get("/").status_code != 500, "非 /api 路径被 secret 配置拖成了 500"

    def test_expired_or_invalid_token_on_a_public_path_is_anonymous(self, client):
        """公开路径上的过期/无效凭据 → 匿名放行，**不是 401**。

        公开的语义是「这里不鉴权」：探针打 `/api/health`、未登录的浏览器读市场列表，
        都带着一个过期 token 也要走得通。身份在这里只用来让门禁判得出账号，判不出来
        就是「没身份」，不是「不许进」。
        """
        for token in (_expired_token(), "not-a-jwt"):
            r = client.get("/api/market/tags", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, (
                f"公开路径被一个过期/无效凭据拦成 {r.status_code}：{r.text[:200]}"
            )

    def test_a_valid_token_is_still_recognized_on_a_public_path(self, client, demo):
        """公开路径上的**有效**凭据必须被认出来 —— 这就是「可选」与「忽略」的差别。

        这条不成立时，`/api/market/*` 的 20 条写路由会全部漏在门禁之外（演示账号能
        发帖、点赞、删评论），而所有读路径看起来都正常。
        """
        r = client.post("/api/market/probe/like", headers=_headers(demo), json={})
        assert _gate_blocked(r), (
            f"公开路径上的有效身份没被认出来：{r.status_code} {r.text[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 锁 1：遍历全部写路由，演示账号可达的集合 == 白名单（+ 门范围外的那批）
# ═══════════════════════════════════════════════════════════════════════════════

def _reads_credentials(route) -> bool:
    """这条路由的依赖树里有没有 `security_scheme`（= 它自己会从凭据解析身份）。

    **故意与 `web/demo_gate._route_reads_credentials` 各写一份**，不是重复：判据必须
    从路由事实现算。回头去问被测对象「你认为该拦谁」，锁就退化成「门说自己拦了谁，
    门就拦了谁」—— 门把某条路由漏判成「不用管」，两边会一起错，锁恒绿。
    """
    def _walk(dep) -> bool:
        for sub in getattr(dep, "dependencies", None) or ():
            if sub.call is security_scheme or _walk(sub):
                return True
        return False

    return _walk(getattr(route, "dependant", None))


def _is_public_path(path: str) -> bool:
    return path in server.PUBLIC_PATHS or path.startswith(server.PUBLIC_PREFIXES)


def test_write_route_sweep_demo_reachable_set_equals_allowlist(client, demo):
    """把生产 app 的**全部**写方法路由逐条打一遍，断言演示账号可达的集合恰好是
    白名单 ∪ 门范围外的那批。

    这条是「新增写接口默认被拦」的机器判据：将来谁加一条写路由而忘了想演示账号，
    它会以「可达集合里多了一条」现形，不必有人记得来改这份用例。

    **门范围外的那批**（判据见下，不是手抄名单）：路径公开 **且** 自己不解析凭据 ——
    中间件对它不解析、路由自己也不解析，凭据在这个端点上不构成「以该账号行事」，
    门禁无从判定，也不该拦。实际是 5 条公开鉴权路由（演示访客要能注册/登录/刷新）
    + 8 条 `/api/inter-node/*` 的 HMAC 机器接口。`/api/market/*` 的 20 条**不在**其中：
    它们虽在公开前缀下，却自己 `Depends(get_current_user)`，所以派得上身份、也必须被拦。
    """
    # 枚举的键是 `(path_format, method小写)`，白名单里的方法是大写 —— 对齐口径。
    routes = route_facts.enumerate_routes()
    writes = {(m.upper(), p) for (p, m) in routes if m.upper() in WRITE_METHODS}

    # 非空守卫：扫描面塌了的话，下面那条「可达集 == …」会退化成空 == 空而恒真。
    assert len(writes) > 100, f"扫描面只看到 {len(writes)} 条写路由 —— 枚举坏了"

    h = _headers(demo)
    reachable = set()
    for (method, path) in sorted(writes):
        r = client.request(method, _url(path), headers=h, json={})
        if not _gate_blocked(r):
            reachable.add((method, path))

    exempt = {
        (m.upper(), p) for (p, m), route in routes.items()
        if m.upper() in WRITE_METHODS and _is_public_path(p) and not _reads_credentials(route)
    }
    assert exempt, "门范围外的那批是空的 —— 判据写错了（公开路径一条都没匹配上）"

    assert reachable == set(DEMO_WRITE_ALLOWLIST) | exempt, (
        "演示账号可达的写路由集合与「白名单 ∪ 门范围外」不符。\n"
        f"  多出来的（默认没拦住）：{sorted(reachable - set(DEMO_WRITE_ALLOWLIST) - exempt)}\n"
        f"  够不着的白名单条目（死条目）：{sorted(set(DEMO_WRITE_ALLOWLIST) - reachable)}\n"
        f"  判据说该放但实测被拦的：{sorted(exempt - reachable)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 锁 2：负控 —— 新注册一条未入白名单的写路由，演示账号访问必须 403
# ═══════════════════════════════════════════════════════════════════════════════

def _solo_app(store, monkeypatch, *, install_first: bool) -> FastAPI:
    monkeypatch.setattr(deps, "_storage", store)
    app = FastAPI()
    if install_first:
        install_demo_gate(app)

    @app.post("/api/_probe_brand_new_write")
    async def _probe() -> dict:
        return {"ok": True}

    if not install_first:
        install_demo_gate(app)
    app.add_middleware(AuthMiddleware)
    return app


def test_new_write_route_is_denied_by_default(store, accounts, monkeypatch):
    demo, plain = accounts
    client = TestClient(_solo_app(store, monkeypatch, install_first=True),
                        raise_server_exceptions=False)

    r = client.post("/api/_probe_brand_new_write", headers=_headers(demo))
    assert r.status_code == 403 and r.json().get("detail") == DEMO_REFUSAL, r.text

    # 同一条路由对非演示账号照旧 —— 否则这条用例只是在测「什么都拦」。
    r = client.post("/api/_probe_brand_new_write", headers=_headers(plain))
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text


def test_install_after_route_registration_is_a_silent_no_op(store, accounts, monkeypatch):
    """**这条锁住的是装配位置的理由本身**，不是门禁的行为。

    框架在**路由创建时**把 `router.dependencies` 快照进那条路由的 dependant 里，
    之后往 `app.router.dependencies` append 是静默 no-op：不报错、门就是不生效。
    所以 `install_demo_gate` 必须在 `app = FastAPI(...)` 之后、**任何**路由登记之前
    调用（生产里它在 `web/server.py` 紧跟 app 构造），不能像 `install_llm_gate`
    那样放进 `_lifespan`。删掉那条顺序约束，这里立刻红。

    退化时（框架将来改成不快照）本条会红 —— 那是**提示**去看
    `install_demo_gate` 的文档串还成不成立，不是门坏了。
    """
    demo, _ = accounts
    client = TestClient(_solo_app(store, monkeypatch, install_first=False),
                        raise_server_exceptions=False)
    r = client.post("/api/_probe_brand_new_write", headers=_headers(demo))
    assert r.status_code == 200, (
        f"路由登记之后再装竟然生效了（{r.status_code}）—— 框架的快照语义变了，"
        "请复核 install_demo_gate 的装配位置约束")


# ═══════════════════════════════════════════════════════════════════════════════
# 锁 3：DEMO_USERNAMES 只在一个地方被读
# ═══════════════════════════════════════════════════════════════════════════════

_GATE_MODULE = "web/demo_gate.py"


def _production_sources() -> list[str]:
    """生产 .py 的扫描面（相对路径，排序稳定）。

    来源 = `git ls-files --cached --others --exclude-standard '*.py'`：`--others` 让
    **尚未 `git add`** 的新文件也进扫描面 —— 只认索引时，新写的读取点对锁是隐形的。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=root, capture_output=True, text=True, encoding="utf-8").stdout
    return sorted(p for p in out.splitlines() if p and not p.startswith(_NON_PROD_TOP))


def test_gate_scan_face_is_not_empty():
    """非空守卫：扫描面塌了的话，下面那条「别处没有」会变成「什么都没扫到」的假绿。"""
    files = _production_sources()
    assert _GATE_MODULE in files, f"扫描面里没有门禁模块：{files[:5]}…"
    assert len(files) > 50, f"生产 .py 只扫到 {len(files)} 个 —— 扫描面坏了"


def test_demo_usernames_env_name_is_read_only_inside_the_gate_module():
    """`DEMO_USERNAMES` 这个名字只许在门禁模块里出现。

    门禁的**唯一性**就落在这一点上：口径若在别处再读一次（第二个 env 名、第二份
    名单、路由里的 `os.getenv`），「谁是演示账号」就有两个答案，而两边不一致时不报错，
    只是静默放行或静默拒绝。

    按**文本**扫而不是只认 `os.getenv("DEMO_USERNAMES")` 这一种写法：`from
    web.demo_gate import DEMO_USERNAMES_ENV` 在别处用，同样是把口径分出去了。
    注释里提到这个名字也算命中 —— 描述机制与被机制引用在这里不需要分家，
    漏报的代价（静默放行）远大于误报的代价（把注释改个写法）。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [
        rel for rel in _production_sources()
        if rel != _GATE_MODULE
        and "DEMO_USERNAMES" in (root / rel).read_text(encoding="utf-8")
    ]
    assert not hits, f"`DEMO_USERNAMES` 在门禁模块之外也被提到了：{hits}"
