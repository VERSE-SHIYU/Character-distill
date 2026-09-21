# -*- coding: utf-8 -*-
"""身份解析收敛的锁：`token → 身份判定` 只有一处实现，三个出口只做形态转换。

**根因不是「重复」而是「漂移」。** 同一段判定写了三份，两份文案已经各走各的：
过期 —— 中间件「Token 已过期，请重新登录」vs `get_current_user`「Token 已过期」；
sub 缺失 —— 中间件「Token 无效」vs `get_current_user`「Token 缺少用户标识」。
所以中心判据是**跨出口一致性**：同一事态在三个出口上必须映射到同一结果。收敛之前，
「过期」与「sub 缺失」两行红 —— 那两行红本身就是漂移存在的证据，不是用例写错。

**期望表写在本文件里，不 import 生产那张表。** import 过来断言等于让被测对象判自己：
表改错时测试跟着一起改，什么也锁不住。

**三个出口怎么观测**：中间件走真 app（`server.app` + 真 `AuthMiddleware`）——
它挂在 ASGI 栈上，没有别的入口；`get_current_user` / `get_optional_user` 是普通协程，
带显式参数直调，不经路由。这样三个出口的观测各自都在真路径上，且互不遮挡
（走路由调 `get_current_user` 的话，中间件会先返回 401，永远看不到第二个出口）。
**唯有一处例外**：锁中间件自己的 scheme 判断时，真 app 反而挡视线 —— 受保护路由也
`Depends(get_current_user)`，会把同一个请求再拒一次，中间件放行了也看不出来。那一处
单独装中间件 + 不装鉴权依赖的探针路由来测（`test_basic_scheme_with_a_valid_jwt_is_not_authenticated`）。

**边界**：锁的是「七事态 × 三出口」的**可观测映射**、两条「没凭据/凭据 scheme 不对时
不读 secret、不通过鉴权」的判据，外加一条静态锁（`jwt.decode` 调用点全仓唯一）。
不锁枚举名、不锁表的数据结构、不锁门禁怎么用这些出口。

Run: pytest tests/test_identity_resolution.py -v
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from starlette.requests import Request

import deps
import server
from routers.auth import (
    JWT_ALGORITHM,
    _create_access_token,
    get_current_user,
    get_jwt_secret,
    get_optional_user,
    security_scheme,
)
from storage.sqlite_store import SQLiteStore

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: 非公开的受保护路径：中间件在这里解析凭据，端点自己也 `Depends(get_current_user)`。
PROTECTED = "/api/history/list"

#: 事态 → 期望的 (状态码, 文案)；`None` = 通过。
#:
#: 文案是**统一后的口径**，正是收敛要达成的结果：过期取中间件那一份（它更完整），
#: sub 缺失取「Token 无效」（与「签名不对」同类，不另立一句）。状态码一律不变。
EXPECTED: dict[str, tuple[int, str] | None] = {
    "ok": None,
    "missing": (401, "请先登录"),
    "expired": (401, "Token 已过期，请重新登录"),
    "invalid": (401, "Token 无效"),
    "no_sub": (401, "Token 无效"),
    "no_user": (401, "用户不存在"),
    "disabled": (403, "账号已被禁用"),
}

#: 七事态 + 两个「非 Bearer 凭据」的变体（它们与 missing 同归一处，见 `_CASE_BUILDERS`）。
STATES = [
    "ok", "missing", "missing_non_bearer", "expired",
    "invalid", "invalid_wrong_secret", "no_sub", "no_user", "disabled",
]

#: 每个事态应收敛到哪一行期望。变体与它归一的那个事态共用一行 —— 这正是「非 Bearer
#: 与无凭据是同一事态」这句话的判据（HTTPBearer 在 scheme 不是 Bearer 时返回 None）。
STATE_ALIAS = {"missing_non_bearer": "missing", "invalid_wrong_secret": "invalid"}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


#: 账号口令散列只算一次：argon2 每次约 0.35s，逐用例算白烧时间。临时库里的丢弃账号。
_PW_HASH = PasswordHash.recommended().hash("Pass1234")


def _mk_user(store: SQLiteStore, username: str) -> dict:
    return _run(store.create_user(f"usr_{uuid.uuid4().hex[:16]}", username, _PW_HASH))


def _encode(sub: str | None, *, expired: bool = False, secret: str | None = None) -> str:
    """按需造 token：`sub=None` 造出「签名有效但没有 sub」的那种。"""
    delta = timedelta(minutes=-1) if expired else timedelta(minutes=30)
    payload: dict = {"exp": datetime.now(timezone.utc) + delta}
    if sub is not None:
        payload["sub"] = sub
    return jwt.encode(payload, secret or get_jwt_secret(), algorithm=JWT_ALGORITHM)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _case_builders(store: SQLiteStore, user: dict) -> dict[str, tuple[dict, HTTPAuthorizationCredentials | None]]:
    """事态 → (中间件要发的头, 直调依赖要传的凭据)。两者必须指同一件事。"""
    good = _create_access_token(user["id"], user["username"], get_jwt_secret())
    stale = _encode(user["id"], expired=True)
    alien = _encode(user["id"], secret="x" * 40)
    return {
        "ok": (_bearer(good), _creds(good)),
        "missing": ({}, None),
        # scheme 不是 Bearer：中间件把它与「没带凭据」归成一处，HTTPBearer 也返回 None。
        "missing_non_bearer": ({"Authorization": "Basic YWJj"}, None),
        "expired": (_bearer(stale), _creds(stale)),
        "invalid": (_bearer("not-a-jwt"), _creds("not-a-jwt")),
        # 签名有效但密钥不对 —— 与随机串同归 invalid，不是另一类事态。
        "invalid_wrong_secret": (_bearer(alien), _creds(alien)),
        "no_sub": (_bearer(_encode(None)), _creds(_encode(None))),
        "no_user": (_bearer(_encode("usr_no_such_user")), _creds(_encode("usr_no_such_user"))),
        "disabled": (_bearer(good), _creds(good)),
    }


# ── 夹具 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "identity.db"))


@pytest.fixture
def user(store):
    return _mk_user(store, "Ident_" + uuid.uuid4().hex[:8])


@pytest.fixture
def client(store, monkeypatch):
    """真 app：中间件只存在于 ASGI 栈上，另搭一个 app 测不到生产那一份。"""
    monkeypatch.setattr(deps, "_storage", store)
    return TestClient(server.app, raise_server_exceptions=False)


# ── 1. 七事态 × 三出口 ───────────────────────────────────────────────────────


def _call_current_user(credentials, store):
    """直调 `get_current_user`：返回 (异常 or None, user or None)。"""
    try:
        return None, _run(get_current_user(
            credentials=credentials, storage=store, secret=get_jwt_secret(),
        ))
    except HTTPException as exc:
        return exc, None


@pytest.mark.parametrize("state", STATES)
def test_every_state_maps_identically_across_the_three_exits(state, store, user, client):
    """同一事态在三个出口上结果一致，且等于统一后的口径。

    中间件与 `get_current_user` 的 (状态码, 文案) 必须逐字相等 —— 这一条就是漂移的
    反面：收敛前「过期」「sub 缺失」两行红。`get_optional_user` 只关心返回身份。
    """
    headers, credentials = _case_builders(store, user)[state]
    if state == "disabled":
        _run(store.set_user_disabled(user["id"], True))

    expected = EXPECTED[STATE_ALIAS.get(state, state)]

    r = client.get(PROTECTED, headers=headers)
    exc, got = _call_current_user(credentials, store)
    optional = _run(get_optional_user(
        credentials=credentials, storage=store, secret=get_jwt_secret(),
    ))
    where = f"[{state}]"

    if expected is None:
        assert r.status_code == 200, f"{where} 中间件该放行，实得 {r.status_code} {r.text[:200]}"
        assert exc is None and got and got["id"] == user["id"], f"{where} get_current_user 该返回身份"
        assert optional.get("id") == user["id"], f"{where} get_optional_user 该返回身份"
        return

    assert (r.status_code, r.json().get("detail")) == expected, f"{where} 中间件出口"
    assert (exc.status_code, exc.detail) == expected, f"{where} get_current_user 出口"
    assert (r.status_code, r.json().get("detail")) == (exc.status_code, exc.detail), \
        f"{where} 两个出口漂移了：中间件 {(r.status_code, r.json().get('detail'))} vs 依赖 {(exc.status_code, exc.detail)}"
    assert optional == {}, f"{where} 非 OK 时 get_optional_user 必须返回空身份，实得 {optional}"


# ── 2. 没凭据的路径不读 secret；非 Bearer 一律不通过鉴权 ────────────────────


def _http_request(headers: dict[str, str]) -> Request:
    """最小 ASGI scope —— 只为把请求头喂给 `security_scheme` 这个被测对象。"""
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    })


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic YWJj"}],
    ids=["no_header", "basic_bogus"],
)
def test_missing_credentials_never_read_the_secret(headers, store, monkeypatch, client):
    """没带凭据的请求不该碰 `JWT_SECRET`。

    中间件若在**知道这条请求带没带凭据之前**就去取 secret，`JWT_SECRET` 未配置时
    「请先登录」会变成 500 —— 鉴权结果被一个与本请求无关的配置改变。所以 secret 以
    **取值函数**传入：先判 MISSING，判到就直接返回，根本不调用它。
    """
    monkeypatch.delenv("JWT_SECRET", raising=False)
    r = client.get(PROTECTED, headers=headers)
    assert (r.status_code, r.json().get("detail")) == (401, "请先登录"), \
        f"没凭据的请求被 secret 配置影响了：{r.status_code} {r.text[:200]}"


def test_basic_scheme_with_a_valid_jwt_is_not_authenticated(store, user, client):
    """`Authorization: Basic <合法 JWT>` **不得**通过鉴权。

    弱化版（`Basic YWJj`）证明不了这件事：它本就不是合法 JWT，即使 scheme 判断被删掉，
    也只是从「请先登录」变成「Token 无效」，状态码仍是 401 —— 判据只剩文案差异，而文案
    不是安全属性。这里配的是**真签出来的 token**，锁的是「不得通过鉴权」本身。

    **中间件出口必须单独装中间件来测。** 拿真 app 的受保护路由测不出来：那条路由自己也
    `Depends(get_current_user)`，会把同一个请求再拒一次，于是中间件放行了也看不见
    —— 实测把 scheme 判断删掉（M2），真 app 那条断言仍然绿。探针路由不装鉴权依赖，
    中间件的决定就是唯一的闸门，放行与否直接暴露成 200/401。
    """
    token = _create_access_token(user["id"], user["username"], get_jwt_secret())
    headers = {"Authorization": f"Basic {token}"}

    probe = FastAPI()
    probe.add_middleware(server.AuthMiddleware)

    @probe.get("/api/probe")
    def _probe(request: Request):
        return {"id": (request.state.user or {}).get("id")}

    r = TestClient(probe, raise_server_exceptions=False).get("/api/probe", headers=headers)
    assert r.status_code != 200, f"Basic + 合法 JWT 通过了中间件鉴权：{r.status_code} {r.text[:200]}"
    assert (r.status_code, r.json().get("detail")) == (401, "请先登录")

    # 依赖侧：直接驱动真实的 `security_scheme`（HTTPBearer）。不手工构造
    # `credentials=None` 代替 —— 那等于把被测的 scheme 判断整个跳过。
    assert _run(security_scheme(_http_request(headers))) is None, \
        "HTTPBearer 对 Basic scheme 给出了凭据 —— 它的 scheme 判断没了"

    # 真 app 上的端到端兜底：不管哪一层拦下的，都不得是 200。
    assert client.get(PROTECTED, headers=headers).status_code != 200


# ── 3. 收敛形态：`jwt.decode` 全仓只有一个调用点 ─────────────────────────────


def _py_files(*roots: pathlib.Path) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in roots:
        out += [p for p in root.rglob("*.py")
                if ".venv" not in p.parts and "node_modules" not in p.parts]
    return out


def _decode_sites(sources: dict[str, str]) -> list[str]:
    """AST 现算：`jwt.decode(...)` / `_jwt_lib.decode(...)` 的调用点，返回 `文件:行`。

    判据落在**调用**上而不是 `dict.get`/`.decode("utf-8")` 这类同名方法：只认
    模块名是 `jwt` / `_jwt_lib` 的那种，避免把字节解码算进来。
    """
    hits: list[str] = []
    for name, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "decode" \
                    and isinstance(func.value, ast.Name) and func.value.id in {"jwt", "_jwt_lib"}:
                hits.append(f"{name}:{node.lineno}")
    return sorted(hits)


def test_jwt_decode_has_exactly_one_call_site():
    """验签只写一次 —— 三份各写一份正是漂移的来源。

    只钉「唯一」与「在哪个模块」，不钉行号：行号是噪声。新增调用点前先回答
    「为什么不能走 `resolve_identity`」。
    """
    synthetic = {"<合成>": (
        "import jwt\n"
        "jwt.decode(t, s, algorithms=['HS256'])\n"   # 2: 命中
        "_jwt_lib.decode(t, s)\n"                     # 3: 命中
        "raw.decode('utf-8')\n"                       # 4: 同名方法，不该命中
    )}
    assert _decode_sites(synthetic) == ["<合成>:2", "<合成>:3"], \
        "扫描器漏掉了合成的调用点，或把字节解码也算进来了 —— 判据面失效"

    sources = {
        p.relative_to(_REPO).as_posix(): p.read_text(encoding="utf-8")
        for p in _py_files(_REPO / "web")
    }
    assert sources, "扫描面为空"
    hits = _decode_sites(sources)
    assert len(hits) == 1, f"验签调用点应只有一处，实得 {hits}"
    assert hits[0].startswith("web/routers/auth.py:"), \
        f"验签应收敛在 routers/auth.py 里，实得 {hits[0]}"


# ── 4. 中间件只解析一次身份：公开/非公开不是两段解析代码 ─────────────────────


def _resolve_sites(src: str) -> list[int]:
    """AST 现算：`resolve_identity(...)` 的调用点行号。

    判据落在**调用**上（`ast.Call` 且 `func` 是那个名字），不认 import、不认字符串、
    不认 `foo.resolve_identity` 这种属性访问 —— 后者不是同一个符号。
    """
    return sorted(
        node.lineno for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "resolve_identity"
    )


def test_the_middleware_resolves_identity_at_exactly_one_call_site():
    """`web/server.py` 里 `resolve_identity` 恰好一处调用。

    「公开路径」与「非公开路径」的差别只该是**失败拦不拦**，不是「解析不解析」：
    一旦写成两处，公开那处与受保护那处就各有一份时机与形状，改一处不会动另一处，
    也不报错 —— 正是这条收敛要消灭的形态。
    """
    synthetic = (
        "def f():\n"
        "    resolve_identity(t, s, st)\n"          # 2: 命中
        "    other.resolve_identity(t, s, st)\n"    # 3: 属性访问，不是同一符号
        "    note = 'resolve_identity(t)'\n"        # 4: 字符串，不是调用
    )
    assert _resolve_sites(synthetic) == [2], \
        "扫描器把属性访问或字符串也算成了调用点 —— 判据面失效"

    hits = _resolve_sites((_REPO / "web" / "server.py").read_text(encoding="utf-8"))
    assert hits, "web/server.py 里找不到 `resolve_identity(...)` —— 中间件不再解析身份了？"
    assert len(hits) == 1, (
        f"中间件解析身份应只有一处调用，实得 web/server.py:{hits}。"
        "公开路径若单开一处解析，两处会各自漂移而互不报错。"
    )
