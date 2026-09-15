"""`GET /api/health/ready` —— 就绪端点（缺陷 41）的行为与它的三条判别面。

**为什么单开一条端点而不是改 `/api/health`**：容器存活与库可用是两件事，合成一条会让
「app 没起来」与「app 起来了但库连不上」共用一个信号 —— 缺陷 41 的病灶正是三层探针
在凭据错时**全绿**，没人分得出来。

**它必须是公开路径**：部署脚本在容器网络外探它，那时还没有 token。

**它绝不能回显异常**：这条路径无 token 可访问，而异常文本里常见 DSN / 主机名 / 库名 /
用户名（缺陷 33 那一类）。故响应体只带状态词，异常只进日志。

**变异（B-3/B-4/B-5，驱动在 `tests/perf/ping_mutations.py`）**：
  - B-3 从 `PUBLIC_PATHS` 摘掉 → `test_reachable_without_token`
  - B-4 503 响应体带上 `str(exc)` → `test_unready_body_does_not_leak_exception_text`
  - B-5 端点不调 ping、直接回 ready → `test_unready_returns_503`
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import route_facts

from deps import get_storage


READY_PATH = "/api/health/ready"


# ── 假 storage：ping 成功 / 失败两种形态 ──────────────────────────────────────

class _Pinger:
    """只实现 ping 的替身 —— 本文件测的是**端点**，不是库（库那边有 `test_storage_ping.py`）。"""

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc
        self.calls = 0

    async def ping(self) -> None:
        self.calls += 1
        if self._exc is not None:
            raise self._exc


@pytest.fixture
def client_factory():
    """装真 app、换掉 storage。真 app 是必要的：`PUBLIC_PATHS` 的判定在它的中间件里。"""
    created: list = []

    def _build(exc: BaseException | None = None) -> TestClient:
        app = route_facts.app()
        pinger = _Pinger(exc)
        app.dependency_overrides[get_storage] = lambda: pinger
        created.append((app, pinger))
        return TestClient(app)

    yield _build
    for app, _ in created:
        app.dependency_overrides.pop(get_storage, None)


# 一条像真的异步驱动异常：DSN、主机、端口、库名、用户名、口令全在里面。
# 这些串**一个都不许**出现在响应体里 —— 逐个断言，不用「不含 exc」这种代理判据。
_FAKE_DSN = "postgresql://cd_app:hunter2@db.internal:5432/distill"
_LEAKY = RuntimeError(
    'connection to server at "db.internal" (10.0.0.9), port 5432 failed: '
    f'password authentication failed for user "cd_app"; dsn={_FAKE_DSN}'
)
_LEAK_STRINGS = (
    _FAKE_DSN,
    str(_LEAKY),
    "hunter2",
    "db.internal",
    "10.0.0.9",
    "5432",
    "cd_app",
    "distill",
    "postgresql",
    "password",
    "RuntimeError",
)


# ── 行为 ──────────────────────────────────────────────────────────────────────

def test_ready_returns_200_when_storage_answers(client_factory):
    client = client_factory()
    resp = client.get(READY_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_unready_returns_503(client_factory):
    """库答不上话 → 503。端点不调 ping 时这条红（B-5）。"""
    client = client_factory(_LEAKY)
    resp = client.get(READY_PATH)
    assert resp.status_code == 503
    assert resp.json() == {"status": "unready"}


def test_unready_body_does_not_leak_exception_text(client_factory):
    """503 的响应体里不许有异常文本的任何一段 —— 这条路径无 token 可访问。"""
    client = client_factory(_LEAKY)
    body = client.get(READY_PATH).text
    leaked = [s for s in _LEAK_STRINGS if s in body]
    assert not leaked, f"就绪端点的响应体里漏出了这些串：{leaked}\n原文：{body}"


def test_reachable_without_token(client_factory):
    """无 token 也必须能探 —— 部署脚本探它的时候还没有 token。

    401 会同时说明「它没在 PUBLIC_PATHS 里」和「探针在部署路径上根本走不通」。
    """
    client = client_factory()
    resp = client.get(READY_PATH)          # 刻意不带 Authorization
    assert resp.status_code in (200, 503), f"公开路径被认证中间件拦下了：{resp.status_code}"
    assert resp.status_code != 401


def test_probe_is_not_a_stub_that_always_answers_ready(client_factory):
    """先验探针看得见失败：同一条端点，ping 抛异常时**必须**变 503。

    没有这一条，上面几条可能只是「端点恒 200」换来的绿。
    """
    ok = client_factory().get(READY_PATH)
    bad = client_factory(_LEAKY).get(READY_PATH)
    assert (ok.status_code, bad.status_code) == (200, 503)


# ── 路由事实锁：这条 op 必须真在装配出来的 app 上 ────────────────────────────

def test_op_exists_in_the_real_app():
    """路径不写成字面量的反面用法：从 route_facts 的两份账本里核对它的存在。

    `enumerate_routes` 从模块级 router 枚举，`openapi()` 从 `app().routes` 出文档 ——
    两份独立账本都点名它，才算它真在 app 上，而不是只在本文件的字符串里。
    """
    op = (READY_PATH, "get")
    enumerated = set(route_facts.enumerate_routes())
    declared, extra = route_facts.census_diff()
    assert op in declared | enumerated, f"{op} 不在任何一份路由账本里"
    assert op in enumerated, f"{op} 只在 OpenAPI 文档里、枚举不到 —— 账本对不上"
    assert op not in extra, f"{op} 枚举得到、文档里没有：两份账本漂移"
