"""compose「门」的封闭锁：探针命令、探针配置、门的消费者，三部分都封闭（缺陷 41）。

**为什么加锁。** 回退到 `pg_isready` 是**静默的**：容器照样 healthy、依赖它的服务照样被
`depends_on: service_healthy` 放行、部署照样成功 —— 只是「凭据对不对」这件事再没人看。
`pg_isready` 回答的是「服务器在不在应答」，不是「我连得上」；官方语义明确说不需要正确的
用户名 / 口令 / 库名。这类回退不红不报错，只能由用例钉住，不能靠评审记得。

**「门」由三部分组成，任何一部分保持开放，门都会静默失效。**

  1. **探针命令**（`healthcheck.test` 里那条 shell）。第一版锁是**开放式**的：逐条断言
     「不含 `pg_isready`」「调了 `psql`」「`-h` 是服务名」「口令容器内展开」「超时关系对」。
     每条都只证明**好的成分在**，证明不了**坏的成分不在**。实测四种改法全部绕过它（两个
     文件同步改，锁全绿）：

         PGHOSTADDR=127.0.0.1 …                    # 前缀多一个赋值：探针指向环回，口令又不用验
         … psql -h postgres --host=127.0.0.1 …     # 后面多一个长选项：覆盖掉 -h
         … > /dev/null; exit 0                     # 多一条命令：恒 exit 0
         … > /dev/null || true                     # 同上

     故改成**封闭语法**：`shlex` 切出 token，整条命令必须**恰好**是下面这个形状，多一个
     token 就精确点名它是什么、为什么危险。

         PGPASSWORD=<容器内变量> PGCONNECT_TIMEOUT=<整数> \\
             psql -h <本服务的键名> -U <容器内变量> -d <容器内变量> -tAc 'select 1' > /dev/null

  2. **探针配置**（`healthcheck` 映射的其余键）。实测两个文件同步加一个 `disable: true`，
     上一版锁**仍然全绿**。`disable` 不是「多了一个无关的键」，它是把这道门整个关掉。故判据是
     **键集合恰好为 {test, interval, timeout, retries}**，多一个键就点名。

  3. **门的消费者**（连这个库的服务有没有真的等它）。`depends_on` 退成短式列表、`condition`
     改成 `service_started`、或者新服务连库却不声明依赖 —— 探针还红着，但没人等它了。
     判据 3 的前提是「这份文件里有消费者」。只作测试库、消费者在宿主机上的单服务编排
     （`docker-compose.test.yml`）没有这个前提，故登记在 `_NO_IN_FILE_CONSUMER` 里 ——
     「无从判断」要写下来，不能默认通过。

**这一版为什么重写：判据不能再对着「代理」判。** 上一版把模型交给两处代理：自己
`yaml.safe_load`，再按字面量认消费者。三条变异本轮实测，**旧锁全绿**（`5 passed`，
只改 prod 一个文件时为如此）：

     Y-1  worker 用 `extends: {service: app}` 继承到连接串，自己不声明 depends_on
     Y-2  worker 只写 `PGHOST: postgres`（值恰好等于服务名），无 depends_on
     Y-3  worker 的连接串主机段写成 `@postgres/db`（斜杠，不是冒号），无 depends_on

  三条都让一个服务连上了库却没人等门，而旧锁一条都不红：
  - **代理一 · 原始 YAML。** `yaml.safe_load` 只看得到人写的那一层，看不见 `extends` 继承、
    看不见 compose 的归一化 —— Y-1 在原始 YAML 里就是一个没有 environment、没有 depends_on
    的服务，干干净净。`<<` 合并键它倒是会展开（PyYAML 自带），这正是代理最难察的地方：
    它有时候是对的。
  - **代理二 · 按字面量子串认消费者。** 旧锁认的是环境变量值里 `@<服务名>:` 这个**子串** ——
    Y-2 的值里根本没有 `@`，Y-3 有 `@` 后面却是 `/`，两条都从识别器底下穿过去了。

  现在模型一律问 `tests/compose_model.py`（`docker compose config --no-env-resolution
  --format json`），锁只对**模型**判：`extends` / `<<` / 短式 `depends_on` / `${VAR}` 插值
  都已展开。`$$VAR` 在模型里仍是两个 `$`（实测：`$$` 收成一个 `$` 发生在容器内的 shell），
  故下面的变量引用判据照旧写 `$$`。两处代理一起删掉。

**这份锁的边界（重要，别当它证明了更多）。** 它只证明**门的三部分都长成那个样子**。形态对
而语义错依然可能（把 `select 1` 换成任何一条永远成功的语句，形态一样合法），**「凭据错时它
真的会红」不是本文件证明的** —— 那由 `tests/perf/credtest_pg_healthcheck.py`（三场景实跑
证据）承担。

**依赖方向。** 本文件是策略层：认识 `postgres` 这个镜像名，允许一张人写的豁免表。事实层
（`tests/compose_model.py`）不认识本仓的任何服务名与文件名 —— 依赖只能自上而下。
"""

from __future__ import annotations

import json
import re
import shlex
from typing import NamedTuple

import pytest

import compose_model
import policy_table

# 本文件里要真跑一次 `docker compose config` 才拿得到有效模型的那几条，各挂这个 mark：
# 本机没有 docker compose 时**显式 skip**（与本仓其余环境需求同一条规矩），CI 用
# `REQUIRE_COMPOSE_TESTS=1` 拒绝跳过。
#
# 剩下的四条（隐式加载文件的检查、时长解析器的契约、豁免理由非空、段表反例全被拒）
# 不碰 compose，故不挂 —— 没有 docker 的环境里它们照样真跑，判据多一条是一条。
_COMPOSE = compose_model.COMPOSE_ENV.skipif("门的锁")

# 断链的运算符：后面接的是**第二条命令**。
#
# 它**不进下面的段表**，因为 `;` 与 `||` 本身都是**合法 shell token** —— 段表判的是
# 「token 像不像那个形状」，这一条判的是「命令有没有在此处结束」。是唯一一条段表表达
# 不了的形状判据，故单独一条策略。
_SECOND_COMMAND_OPS = (";", "||", "&&", "|", "&")

# 有效模型里看到的仍是 `$$NAME`（`$$` 收成一个 `$` 发生在容器内的 shell），
# 也就是「展开发生在容器内」这一层合法的形态是两个 `$`。段表按**正则字符串**收它们
# （`re:` 前缀），故这里是字符串而不是编译过的 pattern。
_VAR_REF = r"^\$\$[A-Za-z_][A-Za-z0-9_]*$"
_INT = r"^\d+$"
# compose 的时长写法：一个或多个 `<数字><单位>` 段落，`10s` / `1m30s` / `500ms` 都合法。
_DURATION_SEG_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_MS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# 判据 1+2：healthcheck 映射的键集合**恰好**是这四个。
_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries"}
# 判据 3：消费者对某个库的 depends_on 条目。`required` 是 compose **归一化注入**的键（人写的
# 那份 YAML 里没有它），故「恰好」里必须容纳它 —— 否则锁会把工具的正常输出判成多余键。
_DEPENDS_ON_KEYS = {"condition", "required"}
_HEALTHY = "service_healthy"

# 判据 3 的三条硬证据（认得出来 = 这个服务连这个库）。三条缺一不可：
# Y-2 / Y-3 就是实测从旧锁的单条字面量判据底下穿过去的两种写法。
_HARD_BY_DSN = "连接串主机段"
_HARD_BY_NAME = "环境变量值恰好是服务名"
_HARD_BY_DEP = "depends_on 点名"


# ── 从有效模型里取东西 ────────────────────────────────────────────────────────

def _services(model: dict) -> dict[str, dict]:
    return dict(model.get("services") or {})


def _image_is_postgres(image: object) -> bool:
    """镜像名的最后一段去掉 tag 之后是不是 `postgres`。

    有效模型里的镜像名已经插过值（`sentinel_PG_IMAGE_REGISTRY/postgres:16-alpine`），故这里
    不再处理 `${...}`，只按 `/` 切段、按 `:` 去 tag —— 带端口的 registry 也切得对。
    """
    return str(image or "").rsplit("/", 1)[-1].split(":")[0] == "postgres"


def _db_services(model: dict) -> dict[str, dict]:
    """这个文件里「镜像为 postgres」的服务 —— 由解析得出，不写死文件名或服务名。

    这条不变量是承重的：新增一个跑 postgres 的编排文件、或把服务键改名，都要自动被覆盖。
    写死文件名的锁在加一份编排文件时不会红 —— 那是「判据看着在管，其实漏着」。
    """
    return {n: s or {} for n, s in _services(model).items()
            if _image_is_postgres((s or {}).get("image"))}


def _env_values(service: dict) -> list[str]:
    """服务声明的环境变量**值**。有效模型里 `environment` 已归一成映射（列表写法也一样）。"""
    env = service.get("environment")
    return [str(v) for v in env.values()] if isinstance(env, dict) else []


def _connect_evidence(service: dict, db: str) -> list[str]:
    """这个服务凭什么被认成「连了 `db`」—— 三条硬证据，可为空。

    (a) 连接串主机段：某个值里出现 `@<服务名>:` 或 `@<服务名>/`；
    (b) 某个值**恰好等于**服务名（`PGHOST: postgres` 这种按 libpq 变量拆开写的写法）；
    (c) `depends_on` 点名了它。

    三条取并集而不是任选其一：只认 `depends_on` 会漏掉「连了库却不声明依赖」这种最该拦的
    写法；只认 `@<服务名>:` 这个字面量会同时漏掉 (b) 与 `@<服务名>/` 两种等价写法（Y-2/Y-3）。
    """
    values = _env_values(service)
    host_re = re.compile(rf"@{re.escape(db)}[:/]")
    ev: list[str] = []
    if any(host_re.search(v) for v in values):
        ev.append(_HARD_BY_DSN)
    if db in values:
        ev.append(_HARD_BY_NAME)
    dep = service.get("depends_on")
    if isinstance(dep, dict) and db in dep:
        ev.append(_HARD_BY_DEP)
    return ev


def _hard_consumers(model: dict, db: str) -> dict[str, str]:
    """`{服务名: 依据}` —— 有硬证据连 `db` 的服务（不含 `db` 自己）。

    依据一并带出去，是为了让报错说得清是哪条路径命中的：删掉 depends_on 却留下连接串，与
    整个不声明依赖，两条变异不该红出同一句话。
    """
    return {n: "、".join(ev) for n, s in _services(model).items()
            if n != db and (ev := _connect_evidence(s or {}, db))}


# ── 豁免表：不连库的服务（理由必须写、必须与现场对账）────────────────────────
# 键 = (文件名, 服务名)，值 = 人写的理由。判据是「谁来保证这个服务不连库」——写不出理由的
# 服务就是没人复核过。表的机械校验（陈旧 / 未登记 / 空理由）走 policy_table，与另两把锁共用。
_NOT_A_DB_CONSUMER: dict[tuple[str, str], str] = {
    ("docker-compose.local.yml", "jaeger"):
        "OTel 收集器：只收 app 上报的 OTLP，无 depends_on、无连接串、无 env_file",
    ("docker-compose.prod.yml", "nginx"):
        "反向代理：depends_on 的是 app（应用层），不直接连库；无连接串、无 env_file",
    ("docker-compose.prod.yml", "fail2ban"):
        "只读 nginx 日志做封禁，无 depends_on、无连接串、无 env_file",
}

# 判据 3 / 4(a) 的**文件级**例外：本文件里根本没有服务会连这个库的那些编排。
#
# 与上面那张表判的不是同一件事：上面的表说「这个服务不连库」，这张说「这份文件里没有
# 消费者可判」。是「无从判断」，不是「判断通过」—— 故单独一张表，理由里写清凭什么。
# 键 = (文件名, 库服务名)，值 = 人写的理由。机械校验（陈旧 / 缺登记 / 空理由）走
# policy_table —— 判据 3 只负责「现场没有消费者的库必须登记在案」这一个方向。
_NO_IN_FILE_CONSUMER: dict[tuple[str, str], str] = {
    ("docker-compose.test.yml", "postgres"):
        "单服务编排：只起一个 PG 给测试用，消费者是宿主机上的 pytest 进程，不在本文件里；"
        "本文件里没有可判的服务，判据 3（消费者必须等门）在此无从判断",
}


def _dbs_without_in_file_consumer() -> set[tuple[str, str]]:
    """现场「本文件内一个硬消费者都没有」的 (文件名, 库服务名) —— 必须与上表对账。"""
    out: set[tuple[str, str]] = set()
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        for db in _db_services(model):
            if not _hard_consumers(model, db):
                out.add((path.name, db))
    return out


def _needs_registration(file_name: str, model: dict) -> set[tuple[str, str]]:
    """该文件里**必须由豁免表登记**的服务：既不是库本身、也没有硬证据连库的那些。

    这就是闭包判据的另一半：一个服务要么是库、要么连库（那就得等门）、要么登记在案。
    第四种情况不存在 —— 而「新服务没人复核过」原来正是从判据底下溜过去的那条路。
    """
    dbs = _db_services(model)
    hard: set[str] = set()
    for db in dbs:
        hard |= set(_hard_consumers(model, db))
    return {(file_name, n) for n in _services(model) if n not in dbs and n not in hard}


def _needs_registration_all() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        out |= _needs_registration(path.name, model)
    return out


def _hard_consumer_pairs() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        for db in _db_services(model):
            out |= {(path.name, n) for n in _hard_consumers(model, db)}
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
    """切成 shell 词。`punctuation_chars=True` 让 `;` / `||` / `>` 各自成 token ——
    默认的 `shlex.split` 会把 `> /dev/null;` 粘成一个词，报错时就点不出那个 `;`。

    **`2>` 切出来是 `2` 与 `>` 两个 token**（实测：`['…','-tAc','select 1','2','>','/dev/null']`）。
    这里原先写的是「`2>` 自成 token」，照着那句推会得出「`t == '2'` 永不匹配 ⇒ 那个判据是死的」
    这个**错的**结论 —— 实测推翻了它（守卫照常触发）。怀疑不等于事实。"""
    return list(shlex.shlex(cmd, posix=True, punctuation_chars=True))


# ── 那条命令的闭语法：形状写在**数据**里，判据只有一处 ────────────────────────
# **为什么形状要搬进数据。** 旧写法是一条判据一段控制流：16 个拒绝点 = 16 个抛错点，
# 于是判别器数由「写了多少种判法」决定（实测这一个 helper 独占 23 条判别器），而真正要
# 防的失效只有**一类** ——「这条命令不是那个形状」，那 16 个只是它的 16 种表现。形状搬进
# 下表之后，逐 token 的比对由 `_scan` 一处做完，**抛错只有一处**，|D| 回到失效种类数。
#
# **代价如实记。** 形状没有消失，只是搬了家：16 条原来一次性跑过的证据，现在挂在每一段
# 的 `violations` 上，由 `test_closed_grammar_rejects_every_listed_shape` **每次都跑**。
# 净 gain 是「从一次性证据变成每次都跑的证据」，**不是**「少写了 17 条」。反过来说，
# 删掉表里的一行 = 那条反例也跟着消失而**没有任何东西会响** —— 唯一的警报是下面那个
# `_MIN_VIOLATIONS` 下界。
#
# **反例挂在段上，不另立一张表。** 合法定义与它的反例同一处：删一行，两者一起消失，
# 不留半截。分两张表就是又一个漂移点（本轮已踩过两次手工清单）。

_GOOD_CMD = ("PGPASSWORD=\"$$POSTGRES_PASSWORD\" PGCONNECT_TIMEOUT=3 psql -h postgres "
             "-U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\" -tAc 'select 1' > /dev/null")

# 反例总数的下界 —— **这次重写唯一新增的保护**（照 `_MIN_TABLE_RAISES = 16` 的先例）。
# 下界值 = 重写当刻实测的反例条数，不是拍的。表里每加一条反例就要把它往上调；不调的话
# 新加的那条日后被删掉照样无声，而这正是上面说的「删一行什么都不会响」。
_MIN_VIOLATIONS = 18


class _Seg(NamedTuple):
    """闭语法里的**一个位置**。

    词法（`accept`）与语义（`allowed` / `names` / `values`）分两处判，因为两种「不符」要
    报出不同的东西：词法不符报**这个 token 是什么**（`mysql` 不是命令体），语义不符报
    **它为什么在这里不行**（`-h` 出现了两次）。

    `accept` 逐字面量比，`re:` 前缀按正则比。
    `names` 非空的段，token 形如 `名字=值`，`values` 按**名字**给值正则；否则每个 token
    后面跟 `arity` 个值 token，`values` 按 **token** 给值正则。
    `must` = 这一段必须各出现恰好一次的那些 token（顺带保证下一段读得到它们）。
    `violations` 收 `(锚点, 换成什么, 期望 kind)`，锚点必须在 `_GOOD_CMD` 里**恰好**出现一次。

    词法/语义/`must` 三处都判 **token 本身**，不判「位置对不对」：反正整条命令必须恰好是
    这个形状，位置错了必然在别处多出或少了 token。
    """
    name: str
    accept: tuple[str, ...]
    why: str
    repeats: bool = False
    arity: int = 0
    allowed: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    must: tuple[str, ...] = ()
    values: tuple[tuple[str, str], ...] = ()
    violations: tuple[tuple[str, str, str], ...] = ()


_KIND_REASON = {
    "unexpected": "多出语法外的 token",
    "missing": "少了必需的片段",
    "duplicate": "出现了两次",
    "bad_value": "值不合法",
}

# 命令结尾之后：不是一段语法，只是「这里本该结束了」。放在表尾，报错时点名它。
_TAIL = _Seg(name="命令结尾之后", accept=(),
             why="命令到此为止，后面不允许任何 token")

_GRAMMAR: tuple[_Seg, ...] = (
    _Seg(
        name="前缀（环境变量赋值）",
        accept=(r"re:^[A-Za-z_][A-Za-z0-9_]*=",),
        why="前缀只允许 PGPASSWORD 与 PGCONNECT_TIMEOUT 这两条 —— 多一条赋值就是多一种"
            "改法（`PGHOSTADDR=127.0.0.1` 会把探针指到环回，口令又不用验了）",
        repeats=True,
        names=("PGPASSWORD", "PGCONNECT_TIMEOUT"),
        must=("PGPASSWORD", "PGCONNECT_TIMEOUT"),
        values=(("PGPASSWORD", _VAR_REF), ("PGCONNECT_TIMEOUT", _INT)),
        violations=(
            ("PGCONNECT_TIMEOUT=3 psql", "PGHOSTADDR=127.0.0.1 PGCONNECT_TIMEOUT=3 psql",
             "unexpected"),
            ("PGCONNECT_TIMEOUT=3 ", "", "missing"),
            ("PGCONNECT_TIMEOUT=3", 'PGPASSWORD="$$X" PGCONNECT_TIMEOUT=3', "duplicate"),
            ('PGPASSWORD="$$POSTGRES_PASSWORD"', "PGPASSWORD=plain", "bad_value"),
            ("PGCONNECT_TIMEOUT=3", "PGCONNECT_TIMEOUT=abc", "bad_value"),
        )),
    _Seg(
        name="命令体",
        accept=("psql",),
        why="命令体只能是 psql —— 只有真发一条查询到服务端才会走认证",
        must=("psql",),
        violations=(
            ("psql", "mysql", "unexpected"),
            (" psql -h postgres -U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\""
             " -tAc 'select 1' > /dev/null", "", "missing"),
        )),
    _Seg(
        name="psql 参数",
        accept=(r"re:^-(?!tAc$)[A-Za-z]",),
        why="psql 只允许 -h / -U / -d 三组参数各恰好一次 —— 多一组 `-p` 这类选项就是把"
            "探针指到别处去",
        repeats=True,
        arity=1,
        allowed=("-h", "-U", "-d"),
        must=("-h", "-U", "-d"),
        values=(("-U", _VAR_REF), ("-d", _VAR_REF)),
        violations=(
            ("-h postgres", "-h postgres -p 5432", "unexpected"),
            ('-U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tAc \'select 1\' > /dev/null',
             '-U "$$POSTGRES_USER" -d', "missing"),
            ("-h postgres ", "-h postgres -h postgres ", "duplicate"),
            ("-h postgres ", "", "missing"),
            ('-U "$$POSTGRES_USER" ', "", "missing"),
            ('"$$POSTGRES_USER"', "plain", "bad_value"),
        )),
    _Seg(
        name="查询参数",
        accept=(r"re:^-tAc$",),
        why="查询参数只能是 `-tAc 'select 1'` —— 换一条永远成功的语句就等于把这道门"
            "变回装饰",
        arity=1,
        must=("-tAc",),
        values=(("-tAc", r"^select 1$"),),
        violations=(
            ("select 1", "select 2", "bad_value"),
            ("-tAc 'select 1' > /dev/null", "-tAc", "missing"),
        )),
    _Seg(
        name="结尾重定向",
        accept=(">",),
        arity=1,
        must=(">",),
        values=((">", r"^/dev/null$"),),
        why="结尾只能是 `> /dev/null` —— 换一个目标（如 `/dev/stderr`）会把认证失败的"
            "原文弄进健康日志，红就失去诊断价值；`2>` 更直接：stderr 根本进不去",
        violations=(
            ("> /dev/null", "2> /dev/null", "unexpected"),
            ("> /dev/null", "> /dev/stderr", "bad_value"),
            ("> /dev/null", "> /dev/null extra", "unexpected"),
        )),
)


def _matches(token: str, accept: tuple[str, ...]) -> bool:
    return any(re.match(p[3:], token) if p.startswith("re:") else token == p for p in accept)


def _break(seg: _Seg, token: str, kind: str, note: str = "") -> str:
    """一处不符的**说法** —— 拼它不发异常，故它不产生判别器。"""
    return f"在「{seg.name}」这一处：{_KIND_REASON[kind]} {token!r}{note}。{seg.why}"


def _scan(toks: list[str]) -> tuple[dict[str, str], str | None]:
    """按 `_GRAMMAR` 逐段走一遍，**返回**第一处不符（`None` = 合法），不抛。

    返回而不是抛，是这次重写的全部要点：边走边抛的话每个拒绝点都要写一条 `raise`，
    |D| 就等于「写了多少种判法」，而那正是覆盖闭合元锁按定义要消灭的东西。

    顺带修掉旧实现的报错方向：逐 token 之比之后，报的是**真正不符的那一个** —— 实测旧
    实现在 `-tAc 'select 2'` 上报 `'-tAc'`、在 `> /dev/stderr` 上报 `'>'`，两条都在指
    别处，红的人得自己再猜一遍。
    """
    parse: dict[str, str] = {}
    i = 0
    for seg in _GRAMMAR:
        seen: list[str] = []
        while i < len(toks) and _matches(toks[i], seg.accept):
            tok = toks[i]
            if seg.names:
                key, _, val = tok.partition("=")
                if key not in seg.names:
                    return parse, _break(seg, tok, "unexpected")
            else:
                key, val = tok, ""
                if seg.allowed and tok not in seg.allowed:
                    return parse, _break(seg, tok, "unexpected")
            if key in seen:
                return parse, _break(seg, key, "duplicate")
            seen.append(key)
            i += 1
            for _ in range(seg.arity):
                if i >= len(toks):
                    return parse, _break(seg, key, "missing", " 后面没有值")
                val = toks[i]
                i += 1
            rule = dict(seg.values).get(key)
            if rule is not None and not re.match(rule, val):
                return parse, _break(seg, val, "bad_value")
            parse[key] = val
            if not seg.repeats:
                break
        if not seen and i < len(toks):
            want = "、".join(seg.must or seg.accept)
            return parse, _break(seg, toks[i], "unexpected", f"（这一段要的是 {want}）")
        absent = [k for k in seg.must if k not in seen]
        if absent:
            return parse, _break(seg, absent[0], "missing")
    if i < len(toks):
        return parse, _break(_TAIL, toks[i], "unexpected")
    return parse, None


def _reject(msg: str) -> None:
    """**整条命令的抛错点只有这一处。** 判别器按 `lock_coverage` 的 (b)/(c) 两类算，
    「调用点」就是判别器 —— 把抛错收到一处，|D| 才与「有多少种判法」脱钩。"""
    raise AssertionError(msg)


# ── 判据 1 + 2：探针命令 + 探针配置 ───────────────────────────────────────────

def _assert_closed_grammar(cmd: str, where: str, db: str, timeout: float,
                           sentinels=frozenset()) -> None:
    """整条命令必须**恰好**是 `_GOOD_CMD` 那个形状（段表见上），且指到 `db` 上。

    除了形状，这里还有四条**策略**判据（宿主插值 / pg_isready / 断链 / 超时关系 / 环回 /
    键名不一致）。它们不进段表，因为判的不是「形状对不对」而是「这个形状的命令被指到了
    哪里」—— `-h 127.0.0.1` 每个 token 都合法，非法的是它的**值**（与 `db` 比才看得出来）。
    """
    toks = _tokens(cmd)

    # 宿主机的 `${...}` 插值：模型里它已经变成哨兵值。先于语法判据报，说清是哪一种改法。
    hit = next((s for s in sorted(sentinels) if s in cmd), None)
    if hit is not None:
        _reject(f"{where} 的 healthcheck 里出现了宿主机插值的结果 {hit!r}：写 `${{...}}` 是"
                "**宿主机展开**，值会固化进容器配置（`docker inspect` 就能读到）；要容器内"
                "展开必须写 `$$`")

    # 最熟悉的那种回退先给一句人话（段表也拦得住它：token 不是 psql）。
    if any("pg_isready" in t for t in toks):
        _reject(f"{where} 的 healthcheck 用 pg_isready：它不验证凭据，凭据错时照样 exit 0，"
                "这道门会退化成装饰")

    # 断链的运算符：放在段表之前，为的是把 token 本身点出来。`; exit 0` / `|| true` 后面
    # 只要还有 token，段表也会判它「结尾之后多出 token」，但那句话说不出「恒 exit 0」。
    for t in toks:
        if t in _SECOND_COMMAND_OPS:
            _reject(f"{where} 的 healthcheck 多出语法外的 token {t!r}：只允许这一条命令；"
                    "它后面接的第二条命令（`; exit 0` / `|| true` 一类）会让探针恒成功，"
                    "凭据再错也照样绿")

    parse, bad = _scan(toks)
    if bad is not None:
        _reject(f"{where} 的探针命令不是那个闭形状：{bad} 允许的形状是 {_GOOD_CMD!r}")

    connect = int(parse["PGCONNECT_TIMEOUT"])
    if connect >= timeout:
        _reject(f"{where} 的 PGCONNECT_TIMEOUT={connect} 不小于 healthcheck timeout={timeout}"
                " —— 连接超时会被 Docker 的探针超时顶掉，「连不上」与「探针挂住」又共用一个"
                "信号")
    if parse["-h"] in _LOOPBACK:
        _reject(f"{where} 的 healthcheck 拿环回地址 {parse['-h']!r} 当 -h：官方镜像里环回走 "
                "trust 认证，探针又会「永远成功」")
    if parse["-h"] != db:
        _reject(f"{where} 的 healthcheck 探的是 {parse['-h']!r}，而本服务的键名是 {db!r} —— "
                "两者不一致时，改服务名或改探针任一都会让这条锁指向别处")


def _assert_healthcheck_config(hc: object, where: str, db: str, sentinels) -> None:
    """整个 healthcheck 映射封闭：键恰好四个、test 形态对、时长与重试合法、命令行合规。"""
    hc_where = f"{where} 的 healthcheck"
    if not isinstance(hc, dict):
        raise AssertionError(
            f"{hc_where} 不是映射（现为 {hc!r}）—— 没有它，`depends_on: service_healthy` "
            "永远等不到健康状态")
    _closed_keys(hc, _HEALTHCHECK_KEYS, hc_where)
    missing = sorted(_HEALTHCHECK_KEYS - set(hc))
    if missing:
        raise AssertionError(
            f"{hc_where} 少了键 {missing}：键集合**恰好**是 {sorted(_HEALTHCHECK_KEYS)}，"
            "缺一个就等于门少了一部分")

    test = hc["test"]
    if not isinstance(test, list) or len(test) != 2 or test[0] != "CMD-SHELL":
        raise AssertionError(
            f"{hc_where} 的 test 必须是**恰好两个元素的列表**、首元素为 'CMD-SHELL'"
            f"（现为 {test!r}）—— 字符串写法与 'CMD' / 'NONE' 都不走容器内的 shell，"
            "`$$` 转义与整条命令的形态都跟着变")

    interval = _duration_seconds(hc["interval"])
    if interval <= 0:
        raise AssertionError(f"{hc_where} 的 interval={hc['interval']!r} 必须大于 0")
    timeout = _duration_seconds(hc["timeout"])
    if timeout <= 0:
        raise AssertionError(f"{hc_where} 的 timeout={hc['timeout']!r} 必须大于 0")

    retries = hc["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise AssertionError(
            f"{hc_where} 的 retries={retries!r} 不是正整数 —— 探针要连续失败 retries 次才判"
            "不健康，0 或负数让这道门判不出「不健康」")

    # 语法判据自己那套文案里已带「的 healthcheck」，故传**不带**后缀的那个 where。
    _assert_closed_grammar(str(test[1]), where, db, timeout, sentinels)


# ── 判据 3：消费者必须真的等这个门 ────────────────────────────────────────────

def _assert_consumer_waits(service: dict, where: str, db: str, how: str) -> None:
    """消费者的 depends_on 必须对 `db` 声明 `condition: service_healthy`。

    `how` 是「这个消费者凭什么被认出来的」（连接串 / 值等于服务名 / depends_on），进报错
    文案 —— 「删掉 depends_on 只留连接串」与「整个不声明依赖」两条变异不该红出同一句话。
    """
    tail = f"（这个消费者由「{how}」认出它连的是 {db!r}）"
    dep = service.get("depends_on")
    if not isinstance(dep, dict) or db not in dep:
        raise AssertionError(
            f"{where} 的 depends_on 里没有 {db!r}：{tail}，却不声明等待 —— 依赖必须写成 "
            f"`{db}: {{condition: service_healthy}}`；短式列表与整个删掉都不带 condition，"
            "容器起来（探针还红着）也会被放行")
    entry = dep[db]
    if not isinstance(entry, dict):
        raise AssertionError(f"{where} 对 {db!r} 的 depends_on 条目不是映射（现为 {entry!r}）")
    _closed_keys(entry, _DEPENDS_ON_KEYS, f"{where} 对 {db!r} 的 depends_on 条目")
    cond = entry.get("condition")
    if cond != _HEALTHY:
        raise AssertionError(
            f"{where} 对 {db!r} 的 condition={cond!r}，不是 'service_healthy' —— "
            "service_started 只等容器起来，不等探针变绿，探针就成了装饰"
            "（短式列表与显式 service_started 在有效模型里归一成同一个值，两条改法都落这里）")


# ── 负控 ──────────────────────────────────────────────────────────────────────

@_COMPOSE
def test_lock_is_not_vacuous():
    """解析出的东西必须非空，否则下面每条 `for ... in ...` 都恒真 —— 判据失效与判据通过
    长得一样，故把「解析器瞎了」单独变成一次红。"""
    files = compose_model.project_files()
    assert files, ("仓库根目录按 `docker-compose*.yml` 找不出任何编排文件 —— "
                   "本文件所有判据都在空转")
    for path in files:
        model = compose_model.effective_model(path)
        dbs = _db_services(model)
        assert dbs, (f"{path.name} 里解析不出任何「镜像为 postgres」的服务 —— "
                     "该文件的判据在空转")
        for db in dbs:
            if (path.name, db) in _NO_IN_FILE_CONSUMER:
                continue
            hard = _hard_consumers(model, db)
            assert hard, (
                f"{path.name}:{db} 解析不出任何硬消费者 —— 判据 3 在空转。本文件里确实没有"
                "服务会连它（例如只给测试用的单服务编排）时，登记进 `_NO_IN_FILE_CONSUMER` "
                "并写清凭什么 —— 「没有消费者」是要写下来的事实，不是默认通过的空白")
            assert any(how != _HARD_BY_DEP for how in hard.values()), (
                f"{path.name}:{db} 的硬消费者全靠 depends_on 认出来 —— 连接串识别器可能是"
                "瞎的。Y-2/Y-3 正是从「只认 `@<服务名>:` 字面量」底下穿过去的两种写法，"
                "这条判据不能只剩一条识别路径成立")


# ── I5：仓库根目录不得有会被隐式加载的编排文件 ────────────────────────────────

def test_no_compose_file_is_picked_up_implicitly():
    """不带 `-f` 的 `docker compose ...` 会吃到仓库根目录下那些默认名 —— 一份都不许有。"""
    present = sorted(p.name for p in compose_model.autoload_files_present())
    assert present == [], (
        f"仓库根目录出现了会被 compose 自动加载的文件 {present}：部署与本地栈一律带 `-f`，"
        "但任何人手敲一次不带 `-f` 的 `docker compose ...` 都会吃到它 —— 仓库里到底哪份"
        "文件在生效，就取决于一个没人写下来的隐式约定。要么删掉它，要么把它纳入本锁的检查")


# ── I1 + I2：整个 healthcheck 映射 + 那条命令的形态 ───────────────────────────

@_COMPOSE
def test_healthcheck_mapping_and_command_are_closed():
    """键恰好四个（`disable` 一律点名），命令行恰好是那个形状。"""
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        sentinels = compose_model.sentinel_values([path])
        for db, service in _db_services(model).items():
            _assert_healthcheck_config(service.get("healthcheck"),
                                       f"{path.name}:{db}", db, sentinels)


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


def test_closed_grammar_rejects_every_listed_shape():
    """段表里每一段挂着的反例都必须被拒 —— 段表是**数据**，这条是让那份数据每次都跑的那条。

    **它自己不产生判别器，这是刻意的。** 报红一律走 `pytest.fail`：`assert`、
    `pytest.raises`、以及自己写的 `raise` 三类都会被 `lock_coverage` 记成判别器，而
    本条的靶子是**段表里的行** —— 用了那三类，判别器数就变成「表里有多少行」，
    正好是这次重写要消灭的那个形状（元锁的规模必须由「要防多少种失效」决定）。

    两个方向都判：反例必须被拒（否则表里的行是纸），且必须**以它声明的那种失效**被拒
    （否则行还在、话已经说错了 —— 实测旧实现就有两条报错指错方向）。下界那条守的是
    「删一行什么都不会响」。
    """
    rows = [(seg, v) for seg in _GRAMMAR for v in seg.violations]
    if len(rows) < _MIN_VIOLATIONS:
        pytest.fail(f"段表里的反例只剩 {len(rows)} 条，少于下限 {_MIN_VIOLATIONS}：删掉一段、"
                    "或删掉某段的 violations，合法定义与它的反例会一起消失，而这是**唯一**"
                    "会响的地方。表里加了新反例就要把 `_MIN_VIOLATIONS` 跟着往上调。")
    for seg, (anchor, repl, kind) in rows:
        if _GOOD_CMD.count(anchor) != 1:
            pytest.fail(f"{seg.name} 段的反例锚点在 _GOOD_CMD 里命中 "
                        f"{_GOOD_CMD.count(anchor)} 次（应恰 1）：{anchor!r}")
        bad_cmd = _GOOD_CMD.replace(anchor, repl)
        if bad_cmd == _GOOD_CMD:
            pytest.fail(f"{seg.name} 段的反例没有改变命令：{anchor!r} -> {repl!r}")
        try:
            _assert_closed_grammar(bad_cmd, "合成", "postgres", 10.0)
        except AssertionError as e:
            if _KIND_REASON[kind] not in str(e):
                pytest.fail(f"{seg.name} 段的反例 {anchor!r} -> {repl!r} 被判成了别的失效"
                            f"（期望「{_KIND_REASON[kind]}」）：{e}")
            continue
        pytest.fail(f"{seg.name} 段的反例 {anchor!r} -> {repl!r} 没被拒"
                    f"（期望「{_KIND_REASON[kind]}」）")


# ── I3：两份定义的一致性 ──────────────────────────────────────────────────────

@_COMPOSE
def test_every_db_healthcheck_is_verbatim_identical():
    """所有此类服务的**整个 healthcheck 映射**必须逐字相同。

    这是「两份定义」这个取舍的兑现方式：prod 与 local 各写一份（部署只 scp prod 那一个
    文件，抽共享会改动两区流程），代价是可能各自漂移。这条锁把漂移变成一次红 —— 只改一份
    （例如只调一份的 interval）不会红在上面的逐条检查里，必须由这里接住。比的是整个映射而
    不是只比 test：只调一份的 interval / retries 同样是漂移，而在只比 test 的锁里那样是静默的。
    """
    by_hc: dict[str, list[str]] = {}
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        for db, service in _db_services(model).items():
            whole = json.dumps(service.get("healthcheck"), ensure_ascii=False, sort_keys=True)
            by_hc.setdefault(whole, []).append(f"{path.name}:{db}")
    assert len(by_hc) == 1, (
        "postgres 的 healthcheck 出现了多份不一致的定义（或一份都没有），"
        f"「改一处、另一处漂移」的代价已经发生：{by_hc}")


# ── I4(a)：消费者必须等门 ─────────────────────────────────────────────────────

@_COMPOSE
def test_every_consumer_waits_for_a_healthy_probe():
    """每个硬消费者都得用 `condition: service_healthy` 等这道门。"""
    for path in compose_model.project_files():
        model = compose_model.effective_model(path)
        for db in _db_services(model):
            for consumer, how in sorted(_hard_consumers(model, db).items()):
                _assert_consumer_waits(_services(model)[consumer] or {},
                                       f"{path.name}:{consumer}", db, how)


# ── I4(b–e)：豁免表与现场对账 ─────────────────────────────────────────────────

@_COMPOSE
def test_every_service_is_accounted_for():
    """闭包：每个服务要么是库本身、要么有硬证据连库、要么登记在豁免表里。

    「新加一个服务却没人复核它连不连库」原来正是从判据底下溜过去的那条路 —— 探针还红着，
    新服务却谁也没等它，而本文件当时一个字都没得说。
    """
    extra = sorted(policy_table.unexpected(_needs_registration_all(), _NOT_A_DB_CONSUMER))
    assert extra == [], (
        f"这些服务既不是库、也没有任何连库的硬证据，却不在豁免表里：{extra}。"
        "要么它其实连库（那就补 `depends_on: {condition: service_healthy}`），要么登记进 "
        "`_NOT_A_DB_CONSUMER` 并写清凭什么断定它不连库")


@_COMPOSE
def test_exemption_table_has_no_stale_entries():
    """表里有、现场却已经不出现的条目 —— 陈旧条目会掩盖同一位置新长出来的漏网。"""
    stale = sorted(policy_table.stale_keys(_NOT_A_DB_CONSUMER, _needs_registration_all()))
    assert stale == [], (
        f"豁免表里这些条目现场已经不需要了：{stale} —— 服务被删/改名，或者它现在有连库的"
        "硬证据（那它就该等门，不该被豁免）。陈旧条目留着，同一位置的漏网就再没人看得见")


def test_exemption_table_reasons_are_not_blank():
    """每条豁免都必须写理由 —— 「豁免了但说不出凭什么」等于没人复核过。"""
    blank = sorted(policy_table.empty_reasons(_NOT_A_DB_CONSUMER))
    assert blank == [], f"豁免表里这些条目没写理由（或理由不是字符串）：{blank}"


@_COMPOSE
def test_exemption_table_does_not_exempt_a_real_consumer():
    """同一个服务不能既被豁免、又有连库的硬证据 —— 两处判据给出相反答案时必须有一次红。"""
    both = sorted(set(_NOT_A_DB_CONSUMER) & _hard_consumer_pairs())
    assert both == [], (
        f"这些服务既在豁免表里、又被硬证据认定连库：{both} —— 豁免表说「它不连库」，识别器"
        "说「它连了」，两处判据相反。删掉豁免条目：它该等门")


@_COMPOSE
def test_no_in_file_consumer_table_is_current():
    """`_NO_IN_FILE_CONSUMER` 与现场对账：既不陈旧、也不缺理由。

    **陈旧那个方向是这张表唯一的报警器。** 判据 3 只在「现场没有消费者却未登记」时红；
    反过来，某份文件后来长出一个消费者、名单却还挂着，**判据 3 会继续跳过它** —— 那条
    消费者于是谁也没等门，而锁全绿。删掉/忘删的代价正落在这里。
    """
    observed = _dbs_without_in_file_consumer()
    stale = sorted(policy_table.stale_keys(_NO_IN_FILE_CONSUMER, observed))
    assert stale == [], (
        f"这些 (文件, 库服务) 现场已经有本文件内的硬消费者了，却还挂在 `_NO_IN_FILE_CONSUMER` "
        f"里：{stale} —— 它现在有消费者可判，该等门，不该被豁免")
    blank = sorted(policy_table.empty_reasons(_NO_IN_FILE_CONSUMER))
    assert blank == [], (
        f"`_NO_IN_FILE_CONSUMER` 里这些条目没写理由（或理由不是字符串）：{blank} —— "
        "「这份文件里为什么没有消费者」是判据 3 唯一的依据，写不出理由等于没人复核过")
