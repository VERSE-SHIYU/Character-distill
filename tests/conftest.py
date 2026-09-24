"""Pytest configuration: sys.path bootstrap + 把测试钉在独立的测试 PG 上。"""

import functools
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "web"))   # web 模块（deps/routers/...）

# ── 测试一律连独立的测试 PG，护住开发库 ────────────────────────────────────
#
# 测试连哪个库，不能由「这台机器恰好有一份 .env」决定：工作树里的 `.env` 把
# DATABASE_URL 指向开发库 `charsim`，一旦被测试用上，跑一次测试就可能改掉开发数据。
# 三件事把这条路钉死（都在任何业务模块被导入之前完成）：
#
#   1. 连接串只在这一处定义，缺省指向 docker-compose.test.yml 起的容器；
#   2. STORAGE_BACKEND / DATABASE_URL 用**强制赋值**而不是 setdefault —— `web/deps.py`
#      的 load_dotenv 不覆盖已存在的变量（`override` 缺省为 False），所以这里先设的值
#      对工作树里的 .env 稳赢；外部显式给的 TEST_DATABASE_URL 仍然生效（本行读它）;
#   3. DB_PATH 指向临时目录：万一还有代码走了 SQLite，也写不进仓库的 data/。
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://charsim:ci_test_password@localhost:55432/charsim_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["STORAGE_BACKEND"] = "postgres"
os.environ["DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="charsim-test-db-"), "charsim.db"
)


def is_test_database(name: str) -> bool:
    """库名是不是测试库 —— 判据只有一条：以 `_test` 结尾。

    写成纯函数是为了能单独测（会话检查是 `pytest.exit`，没法在会话内断言）。
    `charsim_testx` 必须是 False：只查前缀会把开发库的邻居放进来。
    """
    return bool(name) and name.endswith("_test")


def _current_database() -> str | None:
    """真连一次 TEST_DATABASE_URL，取 `SELECT current_database()`；连不上返回 None。"""
    import asyncio

    import asyncpg

    async def _query() -> str:
        conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=5)
        try:
            return await conn.fetchval("SELECT current_database()")
        finally:
            await conn.close()

    try:
        return asyncio.run(_query())
    except Exception:
        return None


def pytest_sessionstart(session):
    """会话开始时核一次「连的真是测试库」—— 不成立就整场中止。

    整场中止而不是逐条 skip：连错库时每一条用例的通过条件都不可信，跑出来的绿也是假的。
    """
    name = _current_database()
    if name is None:
        pytest.exit(
            "测试库连不上（TEST_DATABASE_URL 指向的 PG 不可达）。"
            "先运行 `docker compose -f docker-compose.test.yml up -d` 再跑测试。",
            returncode=1,
        )
    if not is_test_database(name):
        pytest.exit(
            f"你连的不是测试库（库名 {name!r} 不以 _test 结尾）。"
            "测试一律连 docker-compose.test.yml 的 charsim_test，别拿开发库跑。",
            returncode=1,
        )


# 测试用的固定 JWT_SECRET。与上面 STORAGE_BACKEND 同一个理由：这个值由**仓库**提供，
# 不能由「这台机器恰好有没有 .env」决定。
#
# 缺陷 46：没有它时，`tests/test_auth_tokens.py` 的登录/刷新两条会去读工作树里那份
# gitignored `.env` —— 干净检出（或换台机器）上必红，栈底 `routers/auth.py:41
# RuntimeError`。而「本机全绿」这时也不能当证据：它只证明这台机器恰好有 .env。
TEST_JWT_SECRET = "test-only-jwt-secret-not-for-production-use-0123456789abcdef"


@pytest.fixture(autouse=True)
def _jwt_secret_for_tests(monkeypatch):
    """整个测试会话固定一个 JWT_SECRET：任何用例的通过条件都不再是本机的 `.env`。

    用 autouse，**不是**让用例各自声明 —— 那漏一次就滑回 ambient 依赖（缺陷 46 的形态
    本身就是「没人注意到这个值从哪来」）。autouse 让「新来的人完全不知道这件事，写新
    用例也不会出错」成立。

    `load_dotenv` 用的是 setdefault 语义（不覆盖已存在的变量），所以这里设的值对 `.env`
    里的旧值稳赢。CI 的 job 级注入（build.yml 的 gate / upstream-drift 都设了 JWT_SECRET）
    与本条是同一个机制，测试进程内以本条为准 —— 于是 CI 与本地跑的是同一个值，
    不会出现「CI 绿、本地红」这种由环境造成的分歧。

    生产路径照旧从环境读 —— 取值点收敛在 `routers.auth.get_jwt_secret` 这一个函数上，
    它同时是 FastAPI 注入点（`Depends(get_jwt_secret)`），测试可用
    `app.dependency_overrides` 覆盖成别的值，见 `test_auth_tokens.py`。
    """
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)


class EnvironmentRequirement:
    """一条「本用例需要某个外部环境」的声明，把三件事绑在同一处：

      1. 读声明 —— `REQUIRE_<NAME>_TESTS=1`（本环境保证有它）/ `SKIP_<NAME>_TESTS=1`（显式关）；
      2. 探测 —— `probe()` **真去用一次**（不是读某个变量猜，那是代理指标）；
      3. 说清原因 —— skip 原因里写「怎么让它真跑」。

    绑在同一处，是为了让「拒绝跳过」只有一份定义：分散写时各处会各自决定要不要看
    `REQUIRE_*_TESTS`，悄悄滑回静默通道（缺陷 21「豁免即静默放行」同型）。
    """

    def __init__(self, name, probe, *, dependency, short=None, why_unavailable,
                 local_hint, enable_hint):
        self.name = name
        self._probe = probe
        self.dependency = dependency          # skip 原因里的全称，如 PostgreSQL
        self.short = short or name            # skip 原因里末句的简称，如 PG
        self.why_unavailable = why_unavailable
        self.local_hint = local_hint          # 一个完整状语从句，形如「没跑 PG 时」
        self.enable_hint = enable_hint

    def required(self) -> bool:
        """环境是否声明「本环境保证有」。声明之后，依赖它的用例**拒绝跳过**。"""
        return os.getenv(f"REQUIRE_{self.name}_TESTS") == "1"

    def disabled(self) -> bool:
        """环境是否显式关闭（仅本地用，如 SKIP_PG_TESTS=1）。"""
        return os.getenv(f"SKIP_{self.name}_TESTS") == "1"

    def available(self) -> bool:
        """现在真能不能用。probe 自带缓存时进程内只探一次。"""
        return self._probe()

    def skip_reason(self, what: str) -> str:
        """不可用时的可见 skip 原因（把「怎么让它真跑」写在原因里）。"""
        return (
            f"{what}需要真 {self.dependency}，但 {self.why_unavailable}。"
            f"本地{self.local_hint}这是**显式 skip**（不是失败）；要真跑就{self.enable_hint}。"
            f"CI 用 REQUIRE_{self.name}_TESTS=1 声明有 {self.short}，在那里本用例绝不会被跳过。"
        )

    def skipif(self, what: str, *, disabled_label: str | None = None):
        """给用例挂的 mark：不可用且环境未声明「保证有」→ 显式 skip；显式关闭 → skip。

        `disabled_label` 只覆盖「被显式关闭」那句的宾语，默认取 `what`。
        """
        return pytest.mark.skipif(
            self.disabled() or (not self.available() and not self.required()),
            reason=(
                f"SKIP_{self.name}_TESTS=1 显式关闭了 {disabled_label or what}"
                if self.disabled()
                else self.skip_reason(what)
            ),
        )


def requirement(name, probe, **kwargs) -> EnvironmentRequirement:
    """声明一条环境需求，见 `EnvironmentRequirement`。"""
    return EnvironmentRequirement(name, probe, **kwargs)


@functools.lru_cache(maxsize=1)
def _probe_pg() -> bool:
    """`TEST_DATABASE_URL` 指向的库能否真连上 PG（进程内只探一次，不打印凭据）。

    用「真连一次」而不是「读 STORAGE_BACKEND 猜」—— 后者是代理指标：变量写成 postgres
    而库连不上时，用例会以 TypeError/连接错恒红，正是缺陷 25 那类「用代理代替事实」。
    """
    import asyncio

    import asyncpg

    dsn = TEST_DATABASE_URL

    async def _probe() -> bool:
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


PG_ENV = requirement(
    "PG",
    _probe_pg,
    dependency="PostgreSQL",
    short="PG",
    why_unavailable="TEST_DATABASE_URL 指向的库连不上",
    local_hint="没跑 PG 时",
    enable_hint="起 docker-compose.test.yml 并设 TEST_DATABASE_URL",
)


def pg_required() -> bool:
    """环境是否声明「本环境保证有 PG」。CI 用 `REQUIRE_PG_TESTS=1` 声明。

    声明之后，依赖 PG 的用例**拒绝跳过** —— PG 连不上就直接红，而不是默默 skip。
    否则 skip 就成了新的静默通道（缺陷 21「豁免即静默放行」同型）。
    """
    return PG_ENV.required()


def pg_reachable() -> bool:
    """`TEST_DATABASE_URL` 指向的库能否真连上 PG（进程内只探一次，不打印凭据）。"""
    return PG_ENV.available()


def pg_skip_reason(what: str) -> str:
    """PG 不可达时的可见 skip 原因（把「怎么让它真跑」写在原因里）。"""
    return PG_ENV.skip_reason(what)

