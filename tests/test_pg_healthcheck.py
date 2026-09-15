"""postgres 的 healthcheck 必须**真的拿凭据登录一次**（缺陷 41）。

**为什么加锁。** 回退到 `pg_isready` 是**静默的**：容器照样 healthy、`app` 照样被
`depends_on: service_healthy` 放行、部署照样成功 —— 只是「凭据对不对」这件事再没人看。
`pg_isready` 回答的是「服务器在不在应答」，不是「我连得上」；官方语义明确说不需要正确的
用户名 / 口令 / 库名。这类回退不红不报错，只能由用例钉住，不能靠评审记得。

**为什么是封闭语法，不是逐条检查。** 第一版锁是**开放式**的：逐条断言「不含 `pg_isready`」「调了
`psql`」「`-h` 是服务名」「口令容器内展开」「超时关系对」。每条都只证明**好的成分在**，
证明不了**坏的成分不在** —— 评审实测出四种改法全部绕过它（两个文件同步改，锁全绿）：

    PGHOSTADDR=127.0.0.1 …                    # 前缀多一个赋值：探针指向环回，口令又不用验了
    … psql -h postgres --host=127.0.0.1 …     # 后面多一个长选项：覆盖掉 -h
    … > /dev/null; exit 0                     # 多一条命令：恒 exit 0
    … > /dev/null || true                     # 同上

四种都让探针退化成「不验密码」或「永远成功」，而开放式检查一条都不红。故本文件改成
**封闭语法**：`shlex` 切出 token，整条命令必须**恰好**是下面这个形状，多一个 token 就精确
点名它是什么、为什么危险。

    PGPASSWORD=<容器内变量> PGCONNECT_TIMEOUT=<整数> \\
        psql -h <本服务的键名> -U <容器内变量> -d <容器内变量> -tAc 'select 1' > /dev/null

**这份锁的边界（重要，别当它证明了更多）。** 它只证明**命令的形态**。形态对而语义错依然
可能（把 `select 1` 换成任何一条永远成功的语句，形态一样合法），**「凭据错时它真的会红」
不是本文件证明的** —— 那由 `tests/perf/credtest_pg_healthcheck.py`（三场景实跑证据）承担。

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
_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)?$")
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
# YAML 解析后看到的仍是 `$$NAME`：compose 的 `$$` 转义在 compose 解析阶段才收成一个 `$`，
# 也就是「展开发生在容器内的 shell」。故这一层合法的形态是两个 `$`。
_VAR_REF_RE = re.compile(r"^\$\$[A-Za-z_][A-Za-z0-9_]*$")
_INT_RE = re.compile(r"^\d+$")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_MS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}

# psql 只允许这三组参数。判据是「token 在不在白名单里」，不是「它看起来像不像 host」——
#   `--host=` / `--hostaddr` / `-p` 全是白名单外的 token，一律点名。
_PSQL_FLAGS = ("-h", "-U", "-d")

# 断链的运算符：后面接的是**第二条命令**，或一个把输出（含 stderr）吞掉的重定向。
_SECOND_COMMAND_OPS = (";", "||", "&&", "|", "&")

_REQUIRED_ASSIGN = {"PGPASSWORD", "PGCONNECT_TIMEOUT"}


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


def _tokens(cmd: str) -> list[str]:
    """切成 shell 词。`punctuation_chars=True` 让 `;` / `||` / `2>` 各自成 token ——
    默认的 `shlex.split` 会把 `> /dev/null;` 粘成一个词，报错时就点不出那个 `;`。"""
    return list(shlex.shlex(cmd, posix=True, punctuation_chars=True))


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


# ── 封闭语法 ─────────────────────────────────────────────────────────────────

def _assert_closed_grammar(cmd: str, key: str, service: dict) -> None:
    """整条命令必须**恰好**是模块 docstring 里那个形状；否则点名第一个语法外的 token。"""
    name = key.split(":", 1)[1]
    toks = _tokens(cmd)
    pos = 0

    def fail(msg: str) -> None:
        raise AssertionError(f"{key} {msg}")

    def bad(token: str, why: str) -> None:
        fail(f"的 healthcheck 多出语法外的 token {token!r}：{why}")

    # 最熟悉的那种回退先给一句人话（下面的语法也拦得住它：token 不是 psql）。
    if any("pg_isready" in t for t in toks):
        fail("的 healthcheck 用 pg_isready：它不验证凭据，凭据错时照样 exit 0，"
             "这道门会退化成装饰")

    # 断链：运算符与「第二条命令」。放在位置游走之前，为的是把 token 本身点出来。
    for i, t in enumerate(toks):
        if t in _SECOND_COMMAND_OPS:
            bad(t, "只允许这一条命令；它后面接的第二条命令（`; exit 0` / `|| true` 一类）"
                   "会让探针恒成功，凭据再错也照样绿")
        if t == "2" and i + 1 < len(toks) and toks[i + 1] == ">":
            bad("2>", "标准错误被重定向掉了 —— 认证失败的原文进不了健康日志，"
                      "红就失去了诊断价值")

    # ① 前缀：环境变量赋值，变量名集合恰好是 {PGPASSWORD, PGCONNECT_TIMEOUT}
    assigns: dict[str, str] = {}
    while pos < len(toks) and _ASSIGN_RE.match(toks[pos]):
        var, _, value = toks[pos].partition("=")
        if var in assigns:
            bad(toks[pos], f"{var} 出现了两次；只允许 PGPASSWORD 与 PGCONNECT_TIMEOUT 各一次")
        assigns[var] = value
        pos += 1
    extra = [t for t in toks[:pos] if t.split("=", 1)[0] not in _REQUIRED_ASSIGN]
    if extra:
        bad(extra[0], "前缀只允许 PGPASSWORD 与 PGCONNECT_TIMEOUT —— 多一个赋值就是多一条"
                      "改法（`PGHOSTADDR=127.0.0.1` 会把探针指到环回，口令又不用验了）")
    missing = sorted(_REQUIRED_ASSIGN - set(assigns))
    if missing:
        fail(f"的 healthcheck 少了变量赋值 {missing}：{cmd!r}")

    if not _VAR_REF_RE.match(assigns["PGPASSWORD"]):
        fail(f"的 healthcheck 里 PGPASSWORD 的值 {assigns['PGPASSWORD']!r} 不是容器内展开的"
             "变量引用 —— 写成 compose 插值 `${...}` 是**宿主机展开**，"
             "口令值会固化进容器配置，`docker inspect` 就能读到")
    if not _INT_RE.match(assigns["PGCONNECT_TIMEOUT"]):
        fail(f"的 healthcheck 里 PGCONNECT_TIMEOUT 的值 {assigns['PGCONNECT_TIMEOUT']!r} "
             "不是整数")
    connect = int(assigns["PGCONNECT_TIMEOUT"])
    probe = _seconds((service.get("healthcheck") or {}).get("timeout"))
    if probe is None:
        fail("的 healthcheck 拿不到 timeout，无法比较")
    if connect >= probe:
        fail(f"的 PGCONNECT_TIMEOUT={connect} 不小于 healthcheck timeout={probe} —— "
             "连接超时会被 Docker 的探针超时顶掉，「连不上」与「探针挂住」又共用一个信号")

    # ② 命令体：psql
    if pos >= len(toks):
        fail(f"的 healthcheck 到结尾都没有 psql：{cmd!r}")
    if toks[pos] != "psql":
        bad(toks[pos], "命令体只能是 psql —— 只有真发一条查询到服务端才会走认证")
    pos += 1

    # ③ 参数：-h / -U / -d 各恰好一次，别的一律不允许（含 --host= / --hostaddr 等长短形式）
    pairs: dict[str, str] = {}
    while pos < len(toks) and toks[pos].startswith("-") and toks[pos] != "-tAc":
        flag = toks[pos]
        if flag not in _PSQL_FLAGS:
            bad(flag, "psql 只允许 -h / -U / -d 三组参数；任何别的选项（含 `--host=` / "
                      "`--hostaddr` / `-p` 这些长短与 `=` 形式）都是把探针指到别处去的改法")
        if pos + 1 >= len(toks):
            fail(f"的 healthcheck 里 {flag} 后面没有值：{cmd!r}")
        if flag in pairs:
            bad(flag, f"{flag} 出现了两次；三类参数各恰好一次")
        pairs[flag] = toks[pos + 1]
        pos += 2

    if "-h" not in pairs:
        fail("的 healthcheck 省略了 -h：默认走 unix socket，那是 trust 认证，"
             "口令根本没被验过")
    if pairs["-h"] in _LOOPBACK:
        fail(f"的 healthcheck 拿环回地址 {pairs['-h']!r} 当 -h：官方镜像里环回走 trust "
             "认证，探针又会「永远成功」")
    if pairs["-h"] != name:
        fail(f"的 healthcheck 探的是 {pairs['-h']!r}，而本服务的键名是 {name!r} —— "
             "两者不一致时，改服务名或改探针任一都会让这条锁指向别处")
    for flag, what in (("-U", "用户名"), ("-d", "库名")):
        if flag not in pairs:
            fail(f"的 healthcheck 少了 {flag}（{what}）：{cmd!r}")
        if not _VAR_REF_RE.match(pairs[flag]):
            fail(f"的 healthcheck 里 {flag} 的值 {pairs[flag]!r} 不是容器内展开的变量引用")

    # ④ 查询参数只允许 -tAc 'select 1'
    if toks[pos:pos + 2] != ["-tAc", "select 1"]:
        bad(toks[pos] if pos < len(toks) else "<命令到此结束>",
            "查询参数只能是 -tAc 'select 1'；换一条永远成功的语句就等于把这道门变回装饰")

    # ⑤ 结尾只允许 > /dev/null（不吞 stderr —— 见上面的 2> 那条）
    end = pos + 2
    if toks[end:end + 2] != [">", "/dev/null"]:
        bad(toks[end] if end < len(toks) else "<命令到此结束>",
            "结尾只能是 > /dev/null")
    if end + 2 != len(toks):
        bad(toks[end + 2], "命令到此为止，后面不允许任何 token")


# ── 负控 ──────────────────────────────────────────────────────────────────────

def test_lock_is_not_vacuous():
    """负控：解析出的服务集合与每个服务的 test 都必须非空，否则下面的断言全在空转。

    集合为空时下面每一条 `for ... in _postgres_services()` 都恒真 —— 判据失效与判据通过
    长得一样，故这里把「解析器瞎了」单独变成一次红。
    """
    services = _postgres_services()
    assert services, (
        f"在仓库根目录的 {_COMPOSE_GLOB} 里解析不出任何「镜像为 postgres」的服务 —— "
        "解析器瞎了，下面各条锁在空转")
    for key, service in services.items():
        assert _shell_cmd(service), f"{key} 解析不出 CMD-SHELL 形态的 healthcheck test"


# ── 命令形态 ─────────────────────────────────────────────────────────────────

def test_healthcheck_is_exactly_the_closed_grammar():
    """整条命令必须恰好是那个形状 —— 多一个 token 就点名它（缺陷 41 返工，见模块 docstring）。"""
    for key, service in _postgres_services().items():
        _assert_closed_grammar(_shell_cmd(service) or "", key, service)


# ── 两份定义的一致性 ──────────────────────────────────────────────────────────

def test_every_postgres_healthcheck_is_verbatim_identical():
    """所有此类服务的 healthcheck test 必须**逐字相同**。

    这是「两份定义」这个取舍的兑现方式：prod 与 local 各写一份（部署只 scp prod 那一个
    文件，抽共享会改动两区流程），代价是可能各自漂移。这条锁把漂移变成一次红 ——
    只改一份（例如只调一份的 PGCONNECT_TIMEOUT）不会红在上面的逐条检查里，必须由这里接住。
    """
    by_test: dict[str, list[str]] = {}
    for key, service in _postgres_services().items():
        test = (service.get("healthcheck") or {}).get("test")
        by_test.setdefault(json.dumps(test, ensure_ascii=False), []).append(key)
    assert len(by_test) == 1, (
        "postgres 的 healthcheck 出现了多份不一致的定义，"
        f"「改一处、另一处漂移」的代价已经发生：{by_test}")
