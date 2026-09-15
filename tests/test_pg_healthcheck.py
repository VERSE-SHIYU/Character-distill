"""postgres 的 healthcheck 必须**真的拿凭据登录一次**（缺陷 41）。

**为什么加锁。** 回退到 `pg_isready` 是**静默的**：容器照样 healthy、`app` 照样被
`depends_on: service_healthy` 放行、部署照样成功 —— 只是「凭据对不对」这件事再没人看。
`pg_isready` 回答的是「服务器在不在应答」，不是「我连得上」；官方语义明确说不需要正确的
用户名 / 口令 / 库名。这类回退不红不报错，只能由用例钉住，不能靠评审记得。

**「门」由三部分组成，任何一部分保持开放，门都会静默失效。**

  1. **探针命令**（`healthcheck.test` 里那条 shell）。第一版锁是**开放式**的：逐条断言
     「不含 `pg_isready`」「调了 `psql`」「`-h` 是服务名」「口令容器内展开」「超时关系对」。
     每条都只证明**好的成分在**，证明不了**坏的成分不在**。评审实测出四种改法全部绕过它
     （两个文件同步改，锁全绿）：

         PGHOSTADDR=127.0.0.1 …                    # 前缀多一个赋值：探针指向环回，口令又不用验了
         … psql -h postgres --host=127.0.0.1 …     # 后面多一个长选项：覆盖掉 -h
         … > /dev/null; exit 0                     # 多一条命令：恒 exit 0
         … > /dev/null || true                     # 同上

     四种都让探针退化成「不验密码」或「永远成功」，而开放式检查一条都不红。故改成
     **封闭语法**：`shlex` 切出 token，整条命令必须**恰好**是下面这个形状，多一个 token 就
     精确点名它是什么、为什么危险。

         PGPASSWORD=<容器内变量> PGCONNECT_TIMEOUT=<整数> \\
             psql -h <本服务的键名> -U <容器内变量> -d <容器内变量> -tAc 'select 1' > /dev/null

  2. **探针配置**（`healthcheck` 映射的其余键）。第二轮审计实测：两个文件同步加一个
     `disable: true`，上一版锁**仍然全绿**。`disable` 不是「多了一个无关的键」，它是把这
     道门整个关掉；`start_period`、`retries` 的取值同样在改门的语义。故这里的判据是
     **键集合恰好为 {test, interval, timeout, retries}**，多一个键就点名。

  3. **门的消费者**（依赖这个库的服务有没有真的等它）。`depends_on` 被改成短式列表（不带
     `condition`）、`condition` 被改成 `service_started`、或者新服务连库却不声明依赖 ——
     探针还红着，但没人等它了，同样是静默的。故消费者由**解析**得出（`depends_on` 与连接串
     两条路径的并集），且必须写成映射形式的 `condition: service_healthy`。

**这份锁的边界（重要，别当它证明了更多）。** 它只证明**门的三部分都长成那个样子**。形态对
而语义错依然可能（把 `select 1` 换成任何一条永远成功的语句，形态一样合法），**「凭据错时它
真的会红」不是本文件证明的** —— 那由 `tests/perf/credtest_pg_healthcheck.py`（三场景实跑
证据）承担。

**用 YAML 解析，不用 grep。** grep 会把注释、`echo`、失败文案里的 `pg_isready` 也算成
「出现过」或「没出现过」；解析出来的是 compose 真的会执行的那串命令。服务、依赖关系一律由
解析得出，不写死文件名与服务名。
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent

# 只取仓库根目录下的编排文件（不递归）：部署与本地栈都是根目录这几份，
# `data/eval_scratch/**` 是草稿目录，不在范围内。
_COMPOSE_GLOB = "docker-compose*.yml"

_INTERP_RE = re.compile(r"\$\{[^}]*\}")           # `${X}` / `${X:-default}` 整体
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
# YAML 解析后看到的仍是 `$$NAME`：compose 的 `$$` 转义在 compose 解析阶段才收成一个 `$`，
# 也就是「展开发生在容器内的 shell」。故这一层合法的形态是两个 `$`。
_VAR_REF_RE = re.compile(r"^\$\$[A-Za-z_][A-Za-z0-9_]*$")
_INT_RE = re.compile(r"^\d+$")
# compose 的时长写法：一个或多个 `<数字><单位>` 段落，`10s` / `1m30s` / `500ms` 都合法。
_DURATION_SEG_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_MS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# psql 只允许这三组参数。判据是「token 在不在白名单里」，不是「它看起来像不像 host」——
#   `--host=` / `--hostaddr` / `-p` 全是白名单外的 token，一律点名。
_PSQL_FLAGS = ("-h", "-U", "-d")

# 断链的运算符：后面接的是**第二条命令**，或一个把输出（含 stderr）吞掉的重定向。
_SECOND_COMMAND_OPS = (";", "||", "&&", "|", "&")

_REQUIRED_ASSIGN = {"PGPASSWORD", "PGCONNECT_TIMEOUT"}

# 判据 1：healthcheck 映射的键集合**恰好**是这四个。
_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries"}
# 判据 3：消费者对某个库的 depends_on 条目，键集合**恰好**是 {condition}。
_DEPENDS_ON_KEYS = {"condition"}
_HEALTHY = "service_healthy"


def _image_is_postgres(image: object) -> bool:
    """镜像名去掉插值与 tag 之后，最后一段是不是 `postgres`。

    `postgres:16-alpine` 与 `${PG_IMAGE_REGISTRY:-docker.io/library}/postgres:16-alpine`
    都要判成是 —— 后者的插值整段去掉，剩下 `/postgres:16-alpine`。
    """
    s = _INTERP_RE.sub("", str(image or ""))
    s = s.split(":")[0]
    return s.rsplit("/", 1)[-1] == "postgres"


def _compose_docs() -> list[tuple[Path, dict]]:
    """仓库根目录下每份编排文件的 `(路径, 解析结果)`。

    判据 3 必须**按文件**分组判「谁在等这个库」，故解析结果连同文件名一起给出，
    而不是像 `_postgres_services()` 那样先摊平成一个字典。
    """
    out: list[tuple[Path, dict]] = []
    for path in sorted(_REPO.glob(_COMPOSE_GLOB)):
        out.append((path, yaml.safe_load(path.read_text(encoding="utf-8")) or {}))
    return out


def _postgres_services() -> dict[str, dict]:
    """`{"<文件>:<服务键>": service}` —— 服务集合由解析得出，不写死文件名或服务名。

    这条不变量是承重的：新增一个跑 postgres 的编排文件、或把服务键改名，都要自动被覆盖。
    写死 `docker-compose.prod.yml` / `postgres` 就等于在守卫与被守对象之间放第二份副本，
    加一份文件时锁不会红 —— 那是「判据看着在管，其实漏着」。
    """
    out: dict[str, dict] = {}
    for path, doc in _compose_docs():
        for name, service in (doc.get("services") or {}).items():
            if _image_is_postgres((service or {}).get("image")):
                out[f"{path.name}:{name}"] = service or {}
    return out


# ── 两个共用 helper ───────────────────────────────────────────────────────────

def _closed_keys(mapping: dict, allowed: set[str], where: str) -> list[str]:
    """`mapping` 里不在 `allowed` 里的键；非空即当场报错，点名 `where` 与每个键名。

    「键集合恰好是这些」是**封闭判据**：多出来的键不会被忽略，而是被点名。healthcheck 上
    多一个 `disable: true` 就能把探针整个关掉，而这种改法在开放式锁里是完全静默的。
    走到 `return` 就说明没有多余键，故返回值恒为空列表 —— 非空的情形已经在上一行报掉了，
    调用点不必再写一遍判定（也就不会有「有的调用点忘了判」这种漏）。
    """
    extra = sorted(k for k in mapping if k not in allowed)
    if extra:
        raise AssertionError(
            f"{where} 多出键 {extra}：只允许 {sorted(allowed)} —— 多出来的键不会被忽略，"
            "而是被点名。`disable: true` 一类能悄悄把门关掉，新增任何一个键都要有人复核"
            "它对门的影响")
    return extra


def _duration_seconds(value: object) -> float:
    """compose 的时长写法 -> 秒（`10s` / `1m30s` / `500ms`）；解析不了即报错点名。

    **不返回默认值**：`5x` 这种没人看得懂的值如果被当成 0，上层就拿着一个编造的数继续判，
    「红」变成「绿」的门正好开在这里。契约由 `test_duration_parser_has_no_fallback_value`
    钉住。
    """
    text = str(value).strip()
    total = 0.0
    pos = 0
    seen = 0
    for m in _DURATION_SEG_RE.finditer(text):
        if m.start() != pos:
            break
        total += float(m.group(1)) * _MS[m.group(2)]
        pos = m.end()
        seen += 1
    if seen == 0 or pos != len(text):
        raise AssertionError(
            f"无法解析时长 {value!r}：compose 的时长写法是 `10s` / `1m30s` / `500ms` —— "
            "解析不了就报错点名这个值，不返回默认值")
    return total


def _tokens(cmd: str) -> list[str]:
    """切成 shell 词。`punctuation_chars=True` 让 `;` / `||` / `2>` 各自成 token ——
    默认的 `shlex.split` 会把 `> /dev/null;` 粘成一个词，报错时就点不出那个 `;`。"""
    return list(shlex.shlex(cmd, posix=True, punctuation_chars=True))


# ── 判据 3 的识别：谁是这道门的消费者 ─────────────────────────────────────────

def _env_values(service: dict) -> list[str]:
    """服务声明的环境变量**值**：列表写法取 `=` 之后那段，映射写法取值。"""
    env = service.get("environment")
    if isinstance(env, dict):
        return [str(v) for v in env.values()]
    if isinstance(env, list):
        out: list[str] = []
        for item in env:
            text = str(item)
            _, sep, value = text.partition("=")
            out.append(value if sep else text)
        return out
    return []


def _gate_consumers(doc: dict, name: str) -> dict[str, str]:
    """同一个编排文件内，依赖 `name` 这个库的服务 -> **凭什么认出来的**。两条路径的并集：

    (a) `depends_on` 里点名了它（映射写法与短式列表都算，后者正是要被判红的那种）；
    (b) 环境变量的值里出现连接串主机段 `@<name>:`。

    (b) 是必需的：只认 `depends_on` 的话，「连了库却不声明依赖」这种最该拦的写法恰好看不见。
    返回值把「认出来的依据」一并带出去，是为了让报错说得清是哪条路径命中的 —— 否则
    「删掉 depends_on 却留下连接串」这条变异红出来的句子和「改成短式列表」一模一样。
    """
    services = doc.get("services") or {}
    marker = f"@{name}:"
    out: dict[str, str] = {}
    for other, service in services.items():
        if other == name:
            continue
        dep = (service or {}).get("depends_on")
        declared = (isinstance(dep, dict) and name in dep) or (isinstance(dep, list) and name in dep)
        connected = any(marker in v for v in _env_values(service or {}))
        if declared and connected:
            out[other] = "depends_on 与连接串"
        elif declared:
            out[other] = "depends_on"
        elif connected:
            out[other] = "连接串"
    return out


# ── 判据 1：探针命令 + 探针配置 ───────────────────────────────────────────────

def _assert_closed_grammar(cmd: str, key: str, timeout: float) -> None:
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
    if connect >= timeout:
        fail(f"的 PGCONNECT_TIMEOUT={connect} 不小于 healthcheck timeout={timeout} —— "
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


def _assert_healthcheck_config(hc: object, key: str) -> None:
    """整个 healthcheck 映射封闭：键恰好四个、test 形态对、时长与重试合法、命令行合规。"""
    where = f"{key} 的 healthcheck"
    if not isinstance(hc, dict):
        raise AssertionError(
            f"{where} 不是映射（现为 {hc!r}）—— 没有它，`depends_on: service_healthy` "
            "永远等不到健康状态")
    _closed_keys(hc, _HEALTHCHECK_KEYS, where)
    missing = sorted(_HEALTHCHECK_KEYS - set(hc))
    if missing:
        raise AssertionError(
            f"{where} 少了键 {missing}：键集合**恰好**是 {sorted(_HEALTHCHECK_KEYS)}，"
            "缺一个就等于门少了一部分")

    test = hc["test"]
    if not isinstance(test, list) or len(test) != 2 or test[0] != "CMD-SHELL":
        raise AssertionError(
            f"{where} 的 test 必须是**恰好两个元素的列表**、首元素为 'CMD-SHELL'"
            f"（现为 {test!r}）—— 字符串写法与 'CMD' / 'NONE' 都不走容器内的 shell，"
            "`$$` 转义与整条命令的形态都跟着变")

    interval = _duration_seconds(hc["interval"])
    if interval <= 0:
        raise AssertionError(f"{where} 的 interval={hc['interval']!r} 必须大于 0")
    timeout = _duration_seconds(hc["timeout"])
    if timeout <= 0:
        raise AssertionError(f"{where} 的 timeout={hc['timeout']!r} 必须大于 0")

    retries = hc["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise AssertionError(
            f"{where} 的 retries={retries!r} 不是正整数 —— 探针要连续失败 retries 次才判"
            "不健康，0 或负数让这道门判不出「不健康」")

    _assert_closed_grammar(str(test[1]), key, timeout)


# ── 判据 3：消费者必须真的等这个门 ────────────────────────────────────────────

def _assert_consumer_waits(service: dict, where: str, name: str, how: str) -> None:
    """消费者的 depends_on 必须是映射写法，且对 `name` 的条目恰好是 `service_healthy`。

    `how` 是「这个消费者凭什么被认出来的」（`depends_on` / 连接串 / 两者），进报错文案 ——
    「删掉 depends_on 只留连接串」这条变异红出来的句子必须自证它走的是连接串那条路径。
    """
    tail = f"（这个消费者由{how}认出它连的是 {name!r}）"
    dep = service.get("depends_on")
    if not isinstance(dep, dict):
        raise AssertionError(
            f"{where} 的 depends_on 不是映射写法（现为 {dep!r}）：{tail}，"
            f"依赖必须写成 `{name}: {{condition: service_healthy}}` —— 短式列表与整个删掉"
            "都不带 condition，容器起来（探针还红着）也会被放行")
    if name not in dep:
        raise AssertionError(
            f"{where} 的 depends_on 里没有 {name!r}：{tail}，却不声明等待 —— "
            "探针再红也拦不住它启动")
    entry = dep[name]
    if not isinstance(entry, dict):
        raise AssertionError(
            f"{where} 对 {name!r} 的 depends_on 条目不是映射（现为 {entry!r}）")
    _closed_keys(entry, _DEPENDS_ON_KEYS, f"{where} 对 {name!r} 的 depends_on 条目")
    cond = entry.get("condition")
    if cond != _HEALTHY:
        raise AssertionError(
            f"{where} 对 {name!r} 的 condition={cond!r}，不是 'service_healthy' —— "
            "service_started 只等容器起来，不等探针变绿，探针就成了装饰")


# ── 负控 ──────────────────────────────────────────────────────────────────────

def test_lock_is_not_vacuous():
    """负控：解析出的服务集合必须非空，否则下面每条 `for ... in _postgres_services()`
    都恒真 —— 判据失效与判据通过长得一样，故这里把「解析器瞎了」单独变成一次红。"""
    services = _postgres_services()
    assert services, (
        f"在仓库根目录的 {_COMPOSE_GLOB} 里解析不出任何「镜像为 postgres」的服务 —— "
        "解析器瞎了，下面各条锁在空转")


# ── 判据 1：整个 healthcheck 映射 + 那条命令的形态 ────────────────────────────

def test_healthcheck_mapping_and_command_are_closed():
    """键恰好四个（`disable` / `start_period` 一律点名），命令行恰好是那个形状。"""
    for key, service in _postgres_services().items():
        _assert_healthcheck_config(service.get("healthcheck"), key)


def test_duration_parser_has_no_fallback_value():
    """`_duration_seconds` 的契约：解析不了就报错点名，**不得返回默认值**。

    「解析不了就当 0」看似安全，实际是把「没人看得懂这个值」变成一个合法数字 —— 上层的
    `> 0` 与「connect < timeout」会拿着一个编造的数继续判，红变绿的门正好开在这里。
    """
    assert _duration_seconds("10s") == 10
    assert _duration_seconds("1m30s") == 90
    assert _duration_seconds("500ms") == 0.5
    for bad in ("5x", "", "s", "10", "1m30"):
        with pytest.raises(AssertionError):
            _duration_seconds(bad)


# ── 判据 2：两份定义的一致性 ──────────────────────────────────────────────────

def test_every_postgres_healthcheck_is_verbatim_identical():
    """所有此类服务的**整个 healthcheck 映射**必须逐字相同。

    这是「两份定义」这个取舍的兑现方式：prod 与 local 各写一份（部署只 scp prod 那一个
    文件，抽共享会改动两区流程），代价是可能各自漂移。这条锁把漂移变成一次红 ——
    只改一份（例如只调一份的 PGCONNECT_TIMEOUT）不会红在上面的逐条检查里，必须由这里接住。
    比的是整个映射而不是只比 test：只调一份的 interval / retries 同样是漂移，而在只比
    test 的锁里那样是静默的。
    """
    by_hc: dict[str, list[str]] = {}
    for key, service in _postgres_services().items():
        whole = json.dumps(service.get("healthcheck"), ensure_ascii=False, sort_keys=True)
        by_hc.setdefault(whole, []).append(key)
    assert len(by_hc) == 1, (
        "postgres 的 healthcheck 出现了多份不一致的定义，"
        f"「改一处、另一处漂移」的代价已经发生：{by_hc}")


# ── 判据 3：门的消费者 ────────────────────────────────────────────────────────

def test_every_consumer_waits_for_a_healthy_probe():
    """每个消费者都得用 `condition: service_healthy` 等这道门。"""
    for path, doc in _compose_docs():
        services = doc.get("services") or {}
        for name, service in services.items():
            if not _image_is_postgres((service or {}).get("image")):
                continue
            consumers = _gate_consumers(doc, name)
            assert consumers, (
                f"{path.name} 里 {name!r} 这个库解析不出任何消费者（depends_on 与连接串"
                "两条识别路径都没命中）—— 要么依赖被静默删掉，要么识别器瞎了，判据 3 在空转")
            for consumer, how in sorted(consumers.items()):
                _assert_consumer_waits(
                    services[consumer] or {}, f"{path.name}:{consumer}", name, how)
