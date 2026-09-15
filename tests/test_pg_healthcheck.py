"""postgres 的 healthcheck 必须**真的拿凭据登录一次**（缺陷 41）。

**为什么加锁。** 回退到 `pg_isready` 是**静默的**：容器照样 healthy、`app` 照样被
`depends_on: service_healthy` 放行、部署照样成功 —— 只是「凭据对不对」这件事再没人看。
`pg_isready` 回答的是「服务器在不在应答」，不是「我连得上」；官方语义明确说不需要正确的
用户名 / 口令 / 库名。这类回退不红不报错，只能由用例钉住，不能靠评审记得。

**这份锁的边界（重要，别当它证明了更多）。** 它只证明**命令的形态**：不含 `pg_isready`、
调了 `psql`、`-h` 指向服务名、口令容器内展开、连接超时小于探针超时。**「凭据错时它真的
会红」不是本文件证明的** —— 那需要真跑一个库，由 `tests/perf/credtest_pg_healthcheck.py`
（三场景实跑证据）承担。形态对而语义错是完全可能的（例如 `psql` 后面接一条永远成功的
语句），本文件看不出来，故不声称。

**用 YAML 解析，不用 grep。** grep 会把注释、`echo`、失败文案里的 `pg_isready` 也算成
「出现过」或「没出现过」；解析出来的是 compose 真的会执行的那串命令。
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent

# 只取仓库根目录下的编排文件（不递归）：部署与本地栈都是根目录这几份，
# `data/eval_scratch/**` 是草稿目录，不在范围内。
_COMPOSE_GLOB = "docker-compose*.yml"

_INTERP_RE = re.compile(r"\$\{[^}]*\}")           # `${X}` / `${X:-default}` 整体
_PGCONNECT_RE = re.compile(r"\bPGCONNECT_TIMEOUT=(\d+)")
_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)?$")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_MS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


def _image_is_postgres(image: object) -> bool:
    """镜像名去掉插值与 tag 之后，最后一段是不是 `postgres`。

    `postgres:16-alpine` 与 `${PG_IMAGE_REGISTRY:-docker.io/library}/postgres:16-alpine`
    都要判成是 —— 后者的插值整段去掉，剩下 `/postgres:16-alpine`。
    """
    s = _INTERP_RE.sub("", str(image or ""))
    s = s.split(":")[0]
    return s.rsplit("/", 1)[-1] == "postgres"


def _shell_cmd(service: dict) -> str | None:
    """healthcheck 里那条 shell 命令；不是 `CMD-SHELL` 形态就返回 None。"""
    test = (service.get("healthcheck") or {}).get("test")
    if isinstance(test, str):
        return test[len("CMD-SHELL"):].strip() if test.startswith("CMD-SHELL") else test
    if isinstance(test, list) and len(test) >= 2 and test[0] == "CMD-SHELL":
        return str(test[1])
    return None


def _seconds(value: object) -> float | None:
    m = _DURATION_RE.match(str(value or "").strip())
    return int(m.group(1)) * _MS[m.group(2) or "s"] if m else None


def _postgres_services() -> dict[str, dict]:
    """`{"<文件>:<服务键>": service}` —— 服务集合由解析得出，不写死文件名或服务名。

    这条不变量是承重的：新增一个跑 postgres 的编排文件、或把服务键改名，都要自动被覆盖。
    写死 `docker-compose.prod.yml` / `postgres` 就等于在守卫与被守对象之间放第二份副本，
    加一份文件时锁不会红 —— 那是「判据看着在管，其实漏着」。
    """
    out: dict[str, dict] = {}
    for path in sorted(_REPO.glob(_COMPOSE_GLOB)):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, service in (doc.get("services") or {}).items():
            if _image_is_postgres((service or {}).get("image")):
                out[f"{path.name}:{name}"] = service or {}
    return out


# ── 负控 ──────────────────────────────────────────────────────────────────────

def test_lock_is_not_vacuous():
    """负控：解析出的服务集合与每个服务的 test 都必须非空，否则下面的断言全在空转。

    集合为空时上面每一条 `for ... in _postgres_services()` 都恒真 —— 判据失效与判据通过
    长得一样，故这里把「解析器瞎了」单独变成一次红。
    """
    services = _postgres_services()
    assert services, (
        f"在仓库根目录的 {_COMPOSE_GLOB} 里解析不出任何「镜像为 postgres」的服务 —— "
        "解析器瞎了，下面各条锁在空转")
    for key, service in services.items():
        assert _shell_cmd(service), f"{key} 解析不出 CMD-SHELL 形态的 healthcheck test"


# ── 命令形态 ─────────────────────────────────────────────────────────────────

def test_healthcheck_does_not_use_pg_isready():
    """`pg_isready` 不验证凭据 —— 它只握手，服务器在应答就 exit 0。"""
    for key, service in _postgres_services().items():
        cmd = _shell_cmd(service) or ""
        assert "pg_isready" not in cmd, (
            f"{key} 的 healthcheck 用 pg_isready：它不验证凭据，凭据错时照样 exit 0，"
            "这道门会退化成装饰")


def test_healthcheck_logs_in_with_psql():
    """必须调 `psql` —— 只有真发一条查询到服务端才会走认证。"""
    for key, service in _postgres_services().items():
        cmd = _shell_cmd(service) or ""
        assert "psql" in cmd, (
            f"{key} 的 healthcheck 没调用 psql，探的不是「我连得上」：{cmd!r}")


def test_healthcheck_targets_its_own_service_name():
    """`-h` 必须等于该服务**自己在 services 下的键名**，且不能是环回地址或省略。

    为什么不能用环回：官方镜像里 unix socket 与 127.0.0.1 都是 trust 认证，只有非环回地址
    才走 scram 校验口令。写 `-h 127.0.0.1` 会让探针退化成「永远成功」，与 `pg_isready`
    同一种失效 —— 判据看着还在，语义没了。
    """
    for key, service in _postgres_services().items():
        cmd = _shell_cmd(service) or ""
        name = key.split(":", 1)[1]
        toks = shlex.split(cmd)
        assert "-h" in toks, (
            f"{key} 的 healthcheck 省略了 -h：默认走 unix socket，那是 trust 认证，"
            "口令根本没被验过")
        host = toks[toks.index("-h") + 1]
        assert host not in _LOOPBACK, (
            f"{key} 的 healthcheck 拿环回地址 {host!r} 当 -h：官方镜像里环回走 trust 认证，"
            "探针又会「永远成功」")
        assert host == name, (
            f"{key} 的 healthcheck 探的是 {host!r}，而本服务的键名是 {name!r} —— "
            "两者不一致时，改服务名或改探针任一都会让这条锁指向别处")


def test_password_is_expanded_inside_the_container():
    """口令必须以 `$$POSTGRES_PASSWORD` 形式在**容器内**展开。

    `$$` 是 compose 的转义：写进容器配置的是变量名，展开发生在容器内的 shell。写成
    `${POSTGRES_PASSWORD}` 是宿主机插值 —— 口令值会被**固化进容器配置**（`docker inspect`
    可读），既扩大扩散面，又让「改环境文件」对已创建的容器不再生效。
    """
    for key, service in _postgres_services().items():
        cmd = _shell_cmd(service) or ""
        # 先报「用了宿主机插值」这条：写成 `${...}` 时下面那条「没有 $$...」也会红，
        # 但那条说的是另一种成因（字面量或缺变量），红源会指错方向。
        assert "${POSTGRES_PASSWORD" not in cmd, (
            f"{key} 的 healthcheck 用了 compose 插值 ${{POSTGRES_PASSWORD}} —— "
            "那是宿主机展开，口令值会固化进容器配置")
        assert "$$POSTGRES_PASSWORD" in cmd, (
            f"{key} 的 healthcheck 里没有容器内展开的 $$POSTGRES_PASSWORD：{cmd!r}")


def test_connect_timeout_is_under_the_probe_timeout():
    """`PGCONNECT_TIMEOUT` 必须设置，且小于 healthcheck 的 `timeout`。

    不小于时两件事共用一个信号：连接在超时前没回来，探针自己被 Docker 判超时 ——
    「连不上」与「连得慢」分不出来（§四）。
    """
    for key, service in _postgres_services().items():
        cmd = _shell_cmd(service) or ""
        m = _PGCONNECT_RE.search(cmd)
        assert m, f"{key} 的 healthcheck 没设置 PGCONNECT_TIMEOUT：{cmd!r}"
        connect = int(m.group(1))
        probe = _seconds((service.get("healthcheck") or {}).get("timeout"))
        assert probe is not None, f"{key} 的 healthcheck 拿不到 timeout，无法比较"
        assert connect < probe, (
            f"{key} 的 PGCONNECT_TIMEOUT={connect} 不小于 healthcheck timeout={probe} —— "
            "连接超时会被 Docker 的探针超时顶掉，「连不上」与「探针挂住」又共用一个信号")


# ── 两份定义的一致性 ──────────────────────────────────────────────────────────

def test_every_postgres_healthcheck_is_verbatim_identical():
    """所有此类服务的 healthcheck test 必须**逐字相同**。

    这是「两份定义」这个取舍的兑现方式：prod 与 local 各写一份（部署只 scp prod 那一个
    文件，抽共享会改动两区流程），代价是可能各自漂移。这条锁把漂移变成一次红 ——
    只改一份（例如只调一份的 PGCONNECT_TIMEOUT）不会红在上面的逐项检查里，必须由这里接住。
    """
    by_test: dict[str, list[str]] = {}
    for key, service in _postgres_services().items():
        test = (service.get("healthcheck") or {}).get("test")
        by_test.setdefault(json.dumps(test, ensure_ascii=False), []).append(key)
    assert len(by_test) == 1, (
        "postgres 的 healthcheck 出现了多份不一致的定义，"
        f"「改一处、另一处漂移」的代价已经发生：{by_test}")
