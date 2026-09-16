# -*- coding: utf-8 -*-
"""缺陷 41 G 轮变异矩阵驱动 —— 一组共 23 条
（G-1、G-2、G-3、G-4、G-5、G-6、G-7、G-8、G-9、G-10、G-11、G-12、G-13、
G-14、G-15、G-16、G-17、G-18、G-19、G-20、G-21、G-22、G-23）。

**G 轮取代了 A 轮。** A 轮打的是「原始 YAML + 按字面量认消费者」那版锁 —— 那版锁对着
两处**代理**判据，三条实测变异（Y-1 `extends` 继承连接串、Y-2 `PGHOST: postgres`、
Y-3 `@postgres/db`）全部绿着从它底下穿过去。G 轮打的锁一律问
`tests/compose_model.py`（`docker compose config --no-env-resolution --format json`），
判的是 compose **真正执行的那份模型**。A 轮的 24 条里，凡是「探针/配置/消费者长什么样」
的命题都按新锁的判据重写成 G 条目；A-7/A-24 那两条打「锁自己解析器」的也重写成
G-21/G-22（改打事实层）。

**每条对应哪条不变量**（不变量编号见 `tests/test_pg_gate.py` 的 docstring）：
  - **I1 探针命令封闭**：G-1～G-5、G-23 —— 整条 shell 必须**恰好**是那个形状。
  - **I2 探针配置封闭**：G-6～G-8 —— healthcheck 键集合恰好四个、超时关系对。
  - **I3 两份定义逐字一致**：G-9 —— 只改一份是唯一在逐条检查里静默的漂移。
  - **I4 门的消费者闭包**：(a) 消费者必须等 G-10/G-11；(b) 新服务必须被复核
    G-12～G-16；(c) 豁免表不得陈旧 G-17；(d) 理由不得为空 G-18；(e) 不得与识别器
    自相矛盾 G-19。
  - **I5 不得有隐式自动加载**：G-20。
  - **事实层自己的契约**：G-21（求值失败不得退化成 `{}`）、G-22（不得退回原始 YAML）——
    这两条的靶子是事实层自己的锁 `tests/test_compose_model.py`，红源在那份文件里。

**G-7 与 G-16 为什么非有不可。** 它们是「锁读的是有效模型，不是文本」的直接反证：
G-7 用 YAML 合并键 `<<` 从顶层锚点注入 `disable: true`（原始 YAML 里 healthcheck 下只有
一个 `<<`），G-16 让人物用 `extends: {service: app}` 继承连接串、再用 `!reset` 覆盖掉
`depends_on`（原始 YAML 里既没有连接串也没有"不等门"这两个事实）。退回原始 YAML 解析，
这两条立刻绿 —— 这正是 G-22 在事实层锁上要红的那件事。

**G-16 的写法是实测选的，不是猜的。** `extends` 的合并语义里 `depends_on` 是**映射合并**
（子服务另写 `- app` 短式列表、或另写一个映射条目，都只是**并进**父服务的 `postgres`
条目，照样等门）；只有 `!reset`（compose 自己的标签）能真正清掉它。四种写法实测：
短式列表 -> `{app:…, postgres:…}`、显式映射 -> 同上、什么都不写 -> `{postgres:…}`、
`!reset []` -> `null`。

**为什么入库。** 本轮的 commit 与 AGENTS.md 引用了本脚本跑出来的「哪条红、红在哪句」。
数字骑在仓外脚本上追不回来（§四：文档引用的数字，其产数脚本与原始产物也要入库）。

**复用而不是复制。** `_apply` / `_restore` / `_run` 三个执行原语直接从
`tests/perf/route_facts_mutations.py` import —— 锚点「恰一命中」的断言、逐字节还原、
子进程 pytest 取红源，三件事各只有一份实现。本文件只补自己那部分：G 组的变异表、docker
前置门、先验基线门、计数门。**不复制那三个函数的本体**，否则两份实现会各自漂移。

**不变量（与另外两个驱动同一套）**
  1. **docker 前置门**：没有可用的 `docker compose` 时**拒跑**。本矩阵的每条判据都落在
     compose 的有效模型上 —— 求不出模型时「红」「绿」都无从谈起，而一个什么都跑不出来的
     矩阵看起来和全绿一模一样。故拒跑并明确打印，**不记为通过**。
  2. **先验基线**：两把锁（策略锁 + 事实层锁）全绿才开跑。基线不绿时「变异后红」说不清红源。
  3. **锚点恰一命中**：由 `_apply` 断言，0 命中或 2 命中当场拒跑 —— 锚点漂移会静默变成
     「变异没生效」，那是最危险的假绿。
  4. **还原逐字节**：收尾核 sha256；G-20 新建的文件单独清掉并点名残留。

**跑矩阵期间不要编辑任何靶子文件**：`_restore` 会把它们在开跑那一刻的字节写回，
期间的编辑会被静默覆盖（这条已记进 AGENTS.md 缺陷 41 的记账）。

**用法**
    python tests/perf/pg_gate_mutations.py            # 全部 23 条
    python tests/perf/pg_gate_mutations.py --group G  # 同上（本驱动只有 G 组）
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PERF_DIR = pathlib.Path(__file__).resolve().parent
ROOT = PERF_DIR.parents[1]
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import compose_model  # noqa: E402  —— 只为 docker 前置门探一次可用性
import route_facts_mutations as framework  # noqa: E402  —— 执行框架的唯一一份实现

PROD = ROOT / "docker-compose.prod.yml"
LOCAL = ROOT / "docker-compose.local.yml"
LOCK = ROOT / "tests" / "test_pg_gate.py"
CM = ROOT / "tests" / "compose_model.py"

# G-20 会**新建**一个文件：它不在 baseline 里，`_restore` 管不到，故单独列出来清。
_OVERRIDE = ROOT / "docker-compose.override.yml"
_CREATED = (_OVERRIDE,)

TARGETS = (PROD, LOCAL, LOCK, CM)

LOCK_PATH = "tests/test_pg_gate.py"
FACT_LOCK_PATH = "tests/test_compose_model.py"

# ── 锚点 ─────────────────────────────────────────────────────────────────────
# 锚点是与文件逐字比较的，抄错一个转义就变成「0 命中」，而 0 命中由 `_apply` 当场拦下。
# 下面每个锚点在**每个**编排文件里各恰好命中一次（除注明只落一份的）。
_PSQL_LINE = r"""      test: ["CMD-SHELL", "PGPASSWORD=\"$$POSTGRES_PASSWORD\" PGCONNECT_TIMEOUT=3 psql -h postgres -U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\" -tAc 'select 1' > /dev/null"]"""
# prod / local 各自的 `-h` 只在 psql 调用里出现一次，故锚在 `-h` 附近而不是整行 ——
# 整行锚会在「同一行里改两处」时说不清是哪一处生效的。
_SZ_TAIL = "PGCONNECT_TIMEOUT=3 psql -h postgres -U"
_PW_PREFIX = '"CMD-SHELL", "PGPASSWORD='            # G-3：在它前面插一个赋值
_PW_VALUE = r'PGPASSWORD=\"$$POSTGRES_PASSWORD\"'    # G-5：把容器内引用换成宿主机插值
_QUERY_TAIL = "'select 1' > /dev/null\"]"           # G-4：在它后面接第二条命令
_RETRIES_LINE = "      retries: 5\n"                # G-6 / G-7：在它后面加键或加合并键
_TIMEOUT_LINE = "      timeout: 5s\n"               # G-8：写成小于 PGCONNECT_TIMEOUT 的值
_INTERVAL_LINE = "      interval: 10s\n"            # G-9：只改 local 一份
# G-10 / G-11 打的是**消费者**（app 对 postgres 的依赖块），两份文件里形状相同。
_DEPENDS_ON_BLOCK = "    depends_on:\n      postgres:\n        condition: service_healthy\n"
# G-23：只改 local 的服务键名。两空格缩进的 `postgres:` 只在服务定义处出现（依赖块里是六空格）。
# 依赖块里那一处**必须**跟着改名 —— 否则 compose 自己在求值期就报 `depends on undefined
# service "postgres"`，红的是工具而不是本锁，那条 I1 判据根本没被走到（实测如此）。
_SERVICE_KEY = "\n  postgres:\n"
_SERVICE_KEY_REF = "\n      postgres:\n"
# G-7 的顶层锚点：`<<` 只能引用**文档中先出现**的锚，故 x- 键必须落在 services 之前。
_TOP_ANCHOR = "\nservices:\n"
_X_ANCHOR = "\nx-gate-hc: &gate-hc\n  disable: true\n\nservices:\n"
# G-12～G-16 把新服务插在 proc 文件的 nginx 之前（app 已在它前面定义，`extends` 引用得到）。
_WORKER_ANCHOR = "\n  nginx:\n"

# 豁免表里最后一条 —— G-17 / G-18 / G-19 都在它附近下刀。
_LAST_REASON = '        "只读 nginx 日志做封禁，无 depends_on、无连接串、无 env_file",'
_LAST_KEY = '    ("docker-compose.prod.yml", "fail2ban"):'
_TABLE_TAIL = _LAST_REASON + "\n}\n"

# 事实层两处：G-21 打「求值失败不得退化成 {}」，G-22 打「不得退回原始 YAML」。
_G21_OLD = "    return _effective_model(str(Path(path).resolve()))"
_G21_NEW = ("    try:\n"
            "        return _effective_model(str(Path(path).resolve()))\n"
            "    except ComposeFactError:\n"
            "        return {}")
_G22_OLD = ('    out = _compose(["-f", project_file, "--env-file", str(_placeholder_env(project_file)),\n'
            '                    "config", "--no-env-resolution", "--format", "json"])\n'
            '    try:\n'
            '        return json.loads(out)\n'
            '    except json.JSONDecodeError as e:\n'
            '        raise ComposeFactError(\n'
            '            f"compose 的输出不是 JSON（{e}）；前 500 字：{out[:500]}") from e')
_G22_NEW = ('    import yaml\n'
            '    return yaml.safe_load(Path(project_file).read_text(encoding="utf-8"))')


def _both(old: str, new: str) -> list[tuple]:
    """同一处改动同时落在两个编排文件上。

    I3 那条（两份定义逐字一致）在这里是**帮手**而不是干扰：两个文件同步改，那条就不会红，
    红源因此唯一地落在本条要打的那句判据上（只改一份的话两条都会红，说不清是谁在管）。
    """
    return [("repl", PROD, [(old, new)]), ("repl", LOCAL, [(old, new)])]


def _worker(block: str) -> list[tuple]:
    """把一段服务定义插到 prod 的 nginx 之前。"""
    return [("repl", PROD, [(_WORKER_ANCHOR, block + _WORKER_ANCHOR)])]


# 每条 = (编号 + 命题, 靶子测试, [(动作, 文件, 载荷)], 期望, 红源标记)
G_GROUP = [
    # ── I1：探针命令封闭（G-1～G-5、G-23）──────────────────────────────────────
    # 这几条都只改**一条命令**，不碰配置。旧锁是开放式的：逐条证明「好的成分在」，证明不了
    # 「坏的成分不在」—— G-3 / G-4 就是实测绕过它的两种改法。
    ("G-1  两个文件的探针都改回 pg_isready",
     LOCK_PATH,
     _both(_PSQL_LINE,
           '      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]'),
     "RED", "pg_isready"),

    ("G-2  两个文件的 -h 都改为环回 127.0.0.1",
     LOCK_PATH,
     _both(_SZ_TAIL, "PGCONNECT_TIMEOUT=3 psql -h 127.0.0.1 -U"),
     "RED", "环回"),

    ("G-3  两个文件的前缀都加 PGHOSTADDR=127.0.0.1",
     LOCK_PATH,
     _both(_PW_PREFIX, '"CMD-SHELL", "PGHOSTADDR=127.0.0.1 PGPASSWORD='),
     "RED", "PGHOSTADDR"),

    ("G-4  两个文件的末尾都加 `|| true`",
     LOCK_PATH,
     _both(_QUERY_TAIL, "'select 1' > /dev/null || true\"]"),
     "RED", "只允许这一条命令"),

    ("G-5  两个文件的口令都改成宿主机插值 ${POSTGRES_PASSWORD}",
     LOCK_PATH,
     _both(_PW_VALUE, r'PGPASSWORD=\"${POSTGRES_PASSWORD}\"'),
     "RED", "宿主机插值"),

    # ── I2：探针配置封闭（G-6～G-8）───────────────────────────────────────────
    # 命令一字未改，改的是「这道门还开不开、什么时候判」。G-7 用**合并键**注入，是「锁读的是
    # 有效模型」的直接反证：原始 YAML 里 healthcheck 下只有一个 `<<`，什么都看不见。
    ("G-6  两个文件的 healthcheck 都加 disable: true",
     LOCK_PATH,
     _both(_RETRIES_LINE, _RETRIES_LINE + "      disable: true\n"),
     "RED", "['disable']"),

    ("G-7  两个文件都用合并键 `<<` 从顶层锚点注入 disable: true（原始 YAML 里只有 `<<`）",
     LOCK_PATH,
     [("repl", PROD, [(_TOP_ANCHOR, _X_ANCHOR),
                      (_RETRIES_LINE, _RETRIES_LINE + "      <<: *gate-hc\n")]),
      ("repl", LOCAL, [(_TOP_ANCHOR, _X_ANCHOR),
                       (_RETRIES_LINE, _RETRIES_LINE + "      <<: *gate-hc\n")])],
     "RED", "['disable']"),

    ("G-8  两个文件的 timeout 都写成 2s（小于 PGCONNECT_TIMEOUT=3）",
     LOCK_PATH,
     _both(_TIMEOUT_LINE, "      timeout: 2s\n"),
     "RED", "不小于 healthcheck timeout"),

    # ── I3：两份定义逐字一致（G-9）───────────────────────────────────────────
    # 只改一份是唯一在逐条检查里静默的漂移：两份各自都合法，只有放在一起比才看得出来。
    ("G-9  只改 local 一份的 interval（两份定义漂移）",
     LOCK_PATH,
     [("repl", LOCAL, [(_INTERVAL_LINE, "      interval: 11s\n")])],
     "RED", "多份不一致"),

    # ── I4(a)：消费者必须等门（G-10 / G-11）──────────────────────────────────
    # 探针完好无损，改的是「谁在等它」。短式列表与显式 service_started 在有效模型里归一成
    # 同一个值，故两条走同一句报错。
    ("G-10 两个文件的 app 都把 depends_on 改成短式列表",
     LOCK_PATH,
     _both(_DEPENDS_ON_BLOCK, "    depends_on:\n      - postgres\n"),
     "RED", "condition='service_started'"),

    ("G-11 两个文件的 app 都把 condition 改成 service_started",
     LOCK_PATH,
     _both(_DEPENDS_ON_BLOCK,
           "    depends_on:\n      postgres:\n        condition: service_started\n"),
     "RED", "condition='service_started'"),

    # ── I4(b)：新服务必须被复核（G-12～G-16）─────────────────────────────────
    # 五种新服务的写法：三种能被识别出「连了库却没等门」（G-12/G-13 两种硬证据、G-16 靠
    # extends 继承），两种认不出来于是必须登记（G-14/G-15）。红源必须点名 worker。
    ("G-12 prod 新增 worker：连接串主机段写 `@postgres/db`（斜杠），无依赖",
     LOCK_PATH,
     _worker("\n  worker:\n    image: alpine\n    environment:\n"
             "      DATABASE_URL: postgresql://u:p@postgres/db\n"),
     "RED", "worker"),

    ("G-13 prod 新增 worker：`PGHOST: postgres`（值恰好等于服务名），无依赖",
     LOCK_PATH,
     _worker("\n  worker:\n    image: alpine\n    environment:\n      PGHOST: postgres\n"),
     "RED", "worker"),

    ("G-14 prod 新增 worker：只有 env_file，无任何连库证据、无依赖",
     LOCK_PATH,
     _worker("\n  worker:\n    image: alpine\n    env_file:\n      - .env\n"),
     "RED", "worker"),

    ("G-15 prod 新增 worker：无任何配置",
     LOCK_PATH,
     _worker("\n  worker:\n    image: alpine\n"),
     "RED", "worker"),

    ("G-16 prod 新增 worker：`extends` 继承 app 的连接串，`!reset` 覆盖掉 depends_on",
     LOCK_PATH,
     _worker("\n  worker:\n    extends:\n      service: app\n    depends_on: !reset []\n"),
     "RED", "worker"),

    # ── I4(c～e)：豁免表与现场对账（G-17～G-19）───────────────────────────────
    ("G-17 豁免表加一条现场不存在的服务（陈旧条目）",
     LOCK_PATH,
     [("repl", LOCK, [(_TABLE_TAIL,
                       _LAST_REASON + "\n"
                       '    ("docker-compose.prod.yml", "ghost-service"): "现场不存在的服务",\n}\n')])],
     "RED", "test_exemption_table_has_no_stale_entries"),

    ("G-18 豁免表某条理由改成空串",
     LOCK_PATH,
     [("repl", LOCK, [(_LAST_REASON, '        "",')])],
     "RED", "test_exemption_table_reasons_are_not_blank"),

    ("G-19 真正的消费者 app 也塞进豁免表（两处判据自相矛盾）",
     LOCK_PATH,
     [("repl", LOCK, [(_LAST_KEY,
                       '    ("docker-compose.prod.yml", "app"): "矛盾：它明明有连接串",\n'
                       + _LAST_KEY)])],
     "RED", "test_exemption_table_does_not_exempt_a_real_consumer"),

    # ── I5：不得有隐式自动加载（G-20）────────────────────────────────────────
    # 注：新建的 override 文件同时会被 `project_files()` 的 glob 看见（它也叫 docker-compose*.yml），
    # 故本条可能不止红一条断言 —— 「本文件看见的每个编排文件都有 postgres 库」那条负控也会红。
    # 标记检查保证 I5 那句**一定**在红源里。
    ("G-20 仓库根目录新建空的 docker-compose.override.yml（会被隐式加载）",
     LOCK_PATH,
     [("write", _OVERRIDE, "services: {}\n")],
     "RED", "docker-compose.override.yml"),

    # ── 事实层自己的契约（G-21 / G-22）：靶子是事实层那把锁 ───────────────────
    # 这两条不打策略锁 —— 策略锁的红是**下游**现象；事实层自己就该先红，否则「锁读的是有效
    # 模型」这件事就只剩策略层在兜，而策略层的判据可以说不出模型是从哪儿来的。
    ("G-21 effective_model 求值失败时返回 {}（静默放行）",
     FACT_LOCK_PATH,
     [("repl", CM, [(_G21_OLD, _G21_NEW)])],
     "RED", "test_missing_cli_raises_instead_of_returning_empty"),

    ("G-22 effective_model 退回读原始 YAML（两处代理里的第一处）",
     FACT_LOCK_PATH,
     [("repl", CM, [(_G22_OLD, _G22_NEW)])],
     "RED", "test_extends_is_expanded"),

    # ── I1 的另一面：探针与键名必须指同一个服务（G-23）───────────────────────
    ("G-23 只改 local 的服务键名 postgres→db（探针仍写 -h postgres）",
     LOCK_PATH,
     [("repl", LOCAL, [(_SERVICE_KEY, "\n  db:\n"),
                       (_SERVICE_KEY_REF, "\n      db:\n")])],
     "RED", "本服务的键名"),
]

GROUPS = {"G": G_GROUP}

_DOC_STAT = re.compile(r"一组共\s*(\d+)\s*条\s*[（(]([^）)]*)[)）]")
_ID_RE = re.compile(r"G-\d+")


def _live_ids() -> set[str]:
    """变异表里现存的编号集合。整表唯一的编号来源，也是下面自检的被比较项。"""
    out: set[str] = set()
    for items in GROUPS.values():
        for item in items:
            found = _ID_RE.search(item[0])
            assert found, f"变异条目没有编号：{item[0]!r}"
            out.add(found.group(0))
    return out


def _count_gate() -> bool:
    """docstring 声明的**编号集合**必须等于变异表的现数；匹配不到同样拒跑。

    比集合而不是比总数：总数对上、编号对不上同样是漂移，而总数相等时那种漂移正好藏得住
    （删一条补一条）。自检失效与自检通过长得一样，故匹配不到也拒跑。
    """
    m = _DOC_STAT.search(__doc__ or "")
    declared = set(_ID_RE.findall(m.group(2))) if m else None
    stated_total = int(m.group(1)) if m else None
    live = _live_ids()
    if declared is not None and stated_total == len(live) and declared == live:
        return True
    print("\ndocstring 的编号与变异表的现数不一致，拒绝跑（自检失效与自检通过长得一样）：\n"
          f"  文档：共 {stated_total} 条 "
          f"{sorted(declared) if declared is not None else '（条数/括号匹配不到）'}\n"
          f"  现数：共 {len(live)} 条 {sorted(live)}")
    return False


def _docker_gate() -> bool:
    """没有可用的 `docker compose` 时拒跑。

    本矩阵的每条判据都落在 compose 的**有效模型**上（`compose_model.effective_model`
    真去跑一次 `docker compose config`）。求不出模型时，每条变异都是「跑不出来」而不是
    「红」—— 而一个什么都跑不出来的矩阵，打印出来和全绿长得一样。故这里拒跑，**不记为
    通过**（§四：两种成因共用一个信号 = 两种都没有守卫）。
    """
    if compose_model.cli_available():
        return True
    print("\n本环境无法运行此矩阵：本机没有可用的 `docker compose`"
          "（探针真跑了一次 `compose version`）。\n"
          "  每条变异判的都是 compose 的有效模型；求不出模型时「红」与「绿」都无从谈起。\n"
          "  **不记为通过** —— 装好 docker（含 compose v2 插件）后重跑。")
    return False


def _baseline_gate() -> list[str]:
    """先验基线：两把锁全绿才开跑 —— 否则「变异后红」说不清红源。

    事实层那把锁也要绿：G-21/G-22 的红源落在它身上，它自己先红的话，那两条的「红」就
    分不清是变异造成的还是本来就红。
    """
    bad = []
    for target in (LOCK_PATH, FACT_LOCK_PATH):
        summary, _ = framework._run(target)
        print(f"  基线 {target:38s} {summary}")
        if "failed" in summary or "error" in summary:
            bad.append(target)
    return bad


def _restore_all(baseline) -> None:
    """还原既有的靶子，并清掉 G-20 新建的那个文件。"""
    framework._restore(baseline)
    for p in _CREATED:
        if p.exists() and p not in baseline:
            p.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="G", help="要跑的组（本驱动只有 G 组）")
    args = ap.parse_args()

    if not _count_gate():
        return 2
    if not _docker_gate():
        return 2

    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    if _baseline_gate():
        print("\n基线不绿 —— 拒绝跑变异矩阵（红源说不清）。先修基线。")
        return 2

    mismatches: list[str] = []
    wanted = [x.strip() for x in args.group.upper().split(",") if x.strip()]
    for name in [g for g in wanted if g in GROUPS]:
        print(f"\n===== {name} 组 =====")
        for item in GROUPS[name]:
            label, target, edits, expect = item[:4]
            marker = item[4] if len(item) > 4 else None
            framework._apply(edits)
            summary, keep = framework._run(target)
            got = "RED" if ("failed" in summary or "error" in summary) else "green"
            _restore_all(baseline)
            if got != expect:
                mismatches.append(f"{label}：期望 {expect} 实得 {got}")
            if marker and not any(marker in k for k in keep):
                mismatches.append(f"{label}：红源里没有 {marker!r}（红的不是那条断言）")
            print(f"\n### {label}   期望={expect}  实得={got}")
            for k in keep:
                print("   ", k)
            print("   >>", summary)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        same = got == hashlib.sha256(baseline[p]).hexdigest()
        if not same:
            mismatches.append(f"{p.name} 还原后 sha256 不符")
        print(f"  {str(p.relative_to(ROOT)):38s} {same}  {got[:16]}")
    for p in _CREATED:
        if p.exists():
            mismatches.append(f"{p.name} 残留在树里（G-20 新建的文件没清掉）")
        print(f"  {str(p.relative_to(ROOT)):38s} {'不存在' if not p.exists() else '仍在！'}")

    print("\n== 结论 ==")
    if mismatches:
        for m in mismatches:
            print("  MISMATCH", m)
        return 1
    print("  全部符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
