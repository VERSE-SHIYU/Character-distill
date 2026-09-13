# -*- coding: utf-8 -*-
"""边界锁：storage 的读取原语已按身份拆成 `*_owned` / `*_unscoped`，
选 `*_unscoped`（无属主过滤）必须是在这里登记过理由的有意选择。

为什么需要这条锁：6 处越权的根因不是「某个调用点忘了写 if」，而是**读取原语本身没有
身份概念**——`get_text(id)` / `get_session(id)` 签名里没有 user，于是每个调用点都得自己
记得校验，忘一个就漏一个，而漏了没有任何报警。能力拆分后，「读到不属于自己的东西」变成
调用形态问题：选 `*_owned`（SQL 里 `WHERE user_id=?`）就不可能漏。剩下的 `*_unscoped`
每一处都必须是有意为之，且理由落在本文件里——否则新增的静默绕过无人察觉。

与 `tests/test_auth_param_used.py` 互补：那条查 router 层「注入了 user 有没有真用上」，
这条查 storage 层「读取有没有绕过身份」。两条都红，才说明两层都锁住了。

`tests/*` 整目录豁免：测试直接构造/读写资源，没有登录语境，按定义就该用 `*_unscoped`。
豁免在 UNSCOPED_ALLOWLIST 里显式列出（而不是偷偷跳过目录），所以名单腐烂检查照样管它。
"""
import ast
import pathlib
import re
import warnings

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 扫本仓自己的代码。第三方 vendor 目录在 PRUNE_DIRS 里剪掉。
SCAN_ROOTS = ("core", "web", "storage", "mcp_server", "adapters", "services", "scripts", "tests")
PRUNE_DIRS = {"__pycache__", "site-packages", "node_modules", "runtime", "Lib", ".venv", "venv", ".git"}

UNSCOPED_ALLOWLIST = {
    "core/chat_engine.py:_evaluate_affinity":
        "引擎读自身 session_id；ChatEngine 无 user 语境（self._user_id 只在 __init__ 初始化，全仓无赋值点）",
    "core/chat_engine.py:_build_time_awareness_block":
        "同上：引擎读自身 session_id，无调用方身份可传",
    "core/chat_engine.py:generate_reunion_greeting":
        "同上：引擎读自身 session_id，无调用方身份可传",
    "core/affinity_service.py:read_persisted_affinity":
        "唯一持久化亲和力读取入口：按 session_id 读，属主校验在调用方完成"
        "（chat.get_affinity 先 get_session_owned；_rebuild_session / resume_session 上游已验）；"
        "storage duck-typed，本函数无身份参数可用",
    "storage/sqlite_store.py:export_session":
        "存储层导出原语，签名里没有 user；属主校验由调用方端点 history.py:export_session 用 get_session_owned 完成",
    "storage/postgres_store.py:export_session":
        "同上（PG 实现）",
    "storage/sqlite_store.py:save_card":
        "写后回读自身刚 upsert 的行填返回值；此刻加身份过滤会误伤 user_id='' 的匿名卡",
    "storage/postgres_store.py:save_card":
        "同上（PG 实现）",
    "storage/sqlite_store.py:update_card":
        "写后回读自身刚更新的行填返回值；签名里没有 user_id，无处传身份",
    "storage/postgres_store.py:update_card":
        "同上（PG 实现）",
    "storage/sqlite_store.py:fork_card":
        "深拷贝源卡是**他人**的公开卡（fork 的语义就是复制别人的），身份过滤会把源卡滤没",
    "storage/postgres_store.py:fork_card":
        "同上（PG 实现）",
    "mcp_server/server.py:_toolkit_for":
        "MCP stdio 通道无身份语境：进程级服务，按 card_id 路由，可服务任意已蒸馏卡",
    "mcp_server/client_demo.py:_case_a_routing_isolation":
        "演示脚本，无登录语境，按 card_id 取卡验证 toolkit 路由隔离",
    "scripts/diag_tool_use.py:load_card":
        "诊断脚本，card_id 由命令行传入，无登录语境",
    "scripts/run_agent_eval.py:load_card":
        "评测脚本，card_id 由命令行传入，无登录语境",
    "scripts/smoke_eval_e2e.py:_verify_session_affinity":
        "评测脚本：session_id 由脚本自建的临时会话提供，无登录语境",
    "web/routers/text.py:get_text_deletion_impact":
        "admin 跨属主查看删除影响的显式逃生口：先 get_text_owned，拿不到且 user.is_admin 时才落到这里",
    "web/routers/admin.py:admin_review_approve":
        "管理员复核任意用户被 flag 的卡 —— 跨属主就是该职责本身",
    "web/routers/inter_node.py:receive_dm":
        "跨节点 DM 同步信道：HMAC 鉴权无用户身份，按 msg_id 幂等去重",
    "web/cross_border_sync.py:_cross_border_resync_loop":
        "后台重同步循环（60s 扫全库未同步行推给对端节点），无用户身份 —— 跨属主正是该职责",
    "web/routers/group.py:_run_group_affinity":
        "群聊流按 group_id 轮询反应：群会话属主校验在上游（_ensure_group 与各 handler 先 "
        "get_group_session_owned）；反应行按 speaker_card_id 分桶，无属主语义",
    "web/routers/message.py:retract_dm_message":
        "撤回是「仅发送者」的公开规则，非发送者（含非收发双方）须 403 而非 404；"
        "身份过滤会把 403 变 404，故显式无身份读",
    "web/routers/market.py:get_book_versions":
        "公开端点（声明「不需登录」）：按 card_id 取 text_id，随后只返回 public 版本",
    "web/routers/market.py:at_reply":
        "@ 回复只读卡设定：src 卡取 text_id，at 卡已验 visibility=='public'，无属主语义",
    "web/routers/market.py:like_card":
        "点赞对象是他人公开卡（market 语义），身份过滤会把公开卡滤没",
    "web/routers/market.py:delete_comment":
        "管理员跨属主删评论的显式逃生口：非管理员走 get_comment_owned（评论作者∨卡作者）",
    "web/routers/market.py:delete_market_card":
        "管理员跨属主下架的显式逃生口：非管理员走 get_card_owned",
    "tests/*":
        "测试直接构造/读写资源，没有登录语境，按定义就该用 *_unscoped",
}


def _is_unscoped_call(node) -> bool:
    """`x.get_text_unscoped(...)` / `self._storage.get_session_unscoped(...)` 这类调用。"""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.endswith("_unscoped"))


def _parents(tree) -> dict:
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node, parents) -> str:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return "<module>"


def _parse(path: pathlib.Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"))


def _unscoped_call_sites() -> set[str]:
    """全仓扫 `*_unscoped` 调用，返回 {相对路径:函数}；tests/ 收敛成单个 `tests/*` 键。"""
    found: set[str] = set()
    for root_name in SCAN_ROOTS:
        base = REPO_ROOT / root_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in PRUNE_DIRS for part in path.parts):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = _parse(path)
            parents = _parents(tree)
            for node in ast.walk(tree):
                if not _is_unscoped_call(node):
                    continue
                found.add("tests/*" if rel.startswith("tests/") else f"{rel}:{_enclosing_function(node, parents)}")
    return found


def test_no_unscoped_read_outside_allowlist():
    unexpected = _unscoped_call_sites() - set(UNSCOPED_ALLOWLIST)
    assert unexpected == set(), (
        f"新出现绕过属主过滤的读取：{sorted(unexpected)}。"
        "优先改用 *_owned（读的是某个登录用户的资源）；确实无用户语境"
        "（引擎自身、迁移、后台清理、无身份通道）才加进 UNSCOPED_ALLOWLIST 并写明理由。"
    )


def test_allowlist_has_no_stale_entries():
    """反过来：名单里的条目若已消失（改回 *_owned 或删了函数），条目就该删掉，别让名单腐烂。"""
    stale = set(UNSCOPED_ALLOWLIST) - _unscoped_call_sites()
    assert stale == set(), f"UNSCOPED_ALLOWLIST 里的条目已不再命中，请删除：{sorted(stale)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 第二把锁：SQL 事实锁（缺陷 19 commit 5）
#
# 上面那把锁锁的是「名字长什么样」——把 `get_x_unscoped` 改回 `get_x` 它就看不见了。
# 这把锁按 **SQL 事实**判：一个公开读原语，若读了属主表、SQL 里却没有身份谓词、签名也
# 没有身份参数、又不叫 `_unscoped`，就必须在 OWNER_READ_ALLOWLIST 里登记理由。名字只在
# 「声明豁免」时起作用，真源是 SQL（与缺陷 21「锁症状不锁代理」同范式）。
#
# 属主表 = migrations_pg/*.sql 里沿 `_id` 边可达 users 的表；身份列 idcol(T) 是把该表连回
# users 的那一列（cards→user_id、direct_messages→sender_id、user_follows→follower_id…）。
# 「已收窄」= 签名有身份参数 ∨ WHERE 子句（JOIN ... ON 不算过滤，它只配对不筛行）含 idcol
# 谓词。JOIN 陷阱见 AGENTS.md 缺陷 19 的 41→46 记录。
#
# 扫**两后端**（`_BACKENDS`）：只扫 sqlite 会漏「PG 版某原语的 WHERE 少一个身份谓词」——
# 方法集镜像断言只比方法名，看不见 SQL 体。两后端同一判据、同一代码路径（`_scan_class`）。
#
# **已知盲区（有意保留，不是本锁的职责）**：判据里「签名有身份参数」本身就是充分的 —— 所以
# 「签名还留着 `user_id`、但 SQL 已经不再用它过滤」这一类**本锁看不见**（删 `get_text_owned`
# 的 `AND user_id = ?` 而保留形参 → 本锁绿）。挡住它的是**运行期层**：sqlite 侧是
# `tests/test_security_authz.py` 的语义用例，PG 侧是
# `tests/test_postgres_store.py::TestPgOwnedIdentityIsolation`。见 AGENTS.md §四
# 「形态锁与语义用例是两层防线」。
# ═══════════════════════════════════════════════════════════════════════════════

_RE_COMMENT = re.compile(r"--[^\n]*")
_RE_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<t>\w+)\s*\((?P<b>[^;]*?)\)\s*(?:;|$)",
    re.I | re.S)
_RE_ALTER = re.compile(
    r"ALTER\s+TABLE\s+[`\"\[]?(?P<t>\w+)[`\"\]]?\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<c>\w+)",
    re.I)
_RE_COLNAME = re.compile(r"^\s*[`\"\[]?(?P<c>\w+)[`\"\]]?\s+[A-Za-z]")
_RE_SELECT = re.compile(r"\bSELECT\b", re.I)
_RE_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE\s|DELETE\s+FROM|REPLACE\s+INTO|CREATE\s|DROP\s|ALTER\s)",
    re.I)
_RE_FROM = re.compile(r"\b(?:FROM|JOIN)\s+[`\"\[]?(?P<t>\w+)", re.I)

# 身份列的前缀别名：`sender_id` 指 users.id，等等。
_ID_ALIAS = {"user": "users", "sender": "users", "receiver": "users", "reporter": "users",
             "resolver": "users", "admin": "users", "follower": "users", "following": "users",
             "author": "users", "owner": "users", "creator": "users"}
# 签名里出现即视为「带身份参数」。
_ID_PARAMS = {"user_id", "owner_id", "username", "uid", "account_id", "viewer_id"}

# 扫描的两后端：(相对路径, 类名)。两后端应镜像，同一判据各扫一遍取并集。
_BACKENDS = (
    ("storage/sqlite_store.py", "SQLiteStore"),
    ("storage/postgres_store.py", "PostgresStore"),
)

# 读了属主表却没有身份收窄、又非 `_unscoped` 的原语 —— 每一处都必须是有意为之，理由写这。
OWNER_READ_ALLOWLIST = {
    # ── 凭据路径：标识符本身就是凭据，没有「属主」可传 ──
    "get_refresh_token": "auth 凭据路径：token_hash 本身即凭据，按它查行就是认证动作，无属主可传",
    "get_user_by_email": "auth 凭据路径：email 是登录凭据，查行即认证动作，无属主可传",
    # ── 公开面：本来就是给任何人看的 ──
    "get_card_forks": "market 公开面：读任一公开卡的派生列表，无属主语义",
    "get_featured_cards": "market 公开面：精选卡列表（admin 与 market 都读）",
    "get_public_cards_by_text_id": "market 公开面：按 text_id 取公开卡",
    "list_public_cards": "market 公开面：公开卡分页",
    "list_public_cards_total": "market 公开面：公开卡总数（与 list_public_cards 同源）",
    "search_public_cards": "market 公开面：公开卡搜索",
    "search_public_cards_total": "market 公开面：公开卡搜索总数",
    # ── 管理面：跨属主正是职责本身（另有 ADMIN_MANAGEMENT_PRIMITIVES 断言调用点全在 admin.py）──
    "count_distill_tasks": "admin 管理面：全站蒸馏任务计数",
    "get_all_usage_summary": "admin 管理面：全站用量汇总（admin 端点与 export_usage_csv 共用）",
    "get_card_reports_grouped": "admin 管理面：跨属主审阅卡举报",
    "get_comment_reports_grouped": "admin 管理面：跨属主审阅评论举报",
    "get_config_changelog": "admin 管理面：配置变更审计日志",
    "get_dashboard_stats": "admin 管理面：全站看板",
    "get_review_logs": "admin 管理面：跨属主审阅日志",
    "get_usage_quality_stats": "admin 管理面：用量质量统计",
    "list_all_cards_admin": "admin 管理面：全站卡列表",
    "list_all_posts_admin": "admin 管理面：全站帖列表",
    "list_distill_tasks": "admin 管理面：全站蒸馏任务列表",
    # ── 属主校验的「第一步」，不是「有意跨属主读」──
    "get_card_author_id":
        "identity_primitive：属主校验的第一步，返回 user_id 供调用方比对（memory/market）。"
        "它存在的意义就是**发现**属主，故不能带身份参数，也非有意跨属主读；"
        "只要「是不是我的卡」请用 get_card_owned",
}

# 管理原语：跨属主读全站数据，前提是「只有管理员路径能到」。断言把这条前提变成事实：
# 生产调用点必须全在 web/routers/admin.py（tests/ 豁免，测试无登录语境）。
ADMIN_MANAGEMENT_PRIMITIVES = (
    "count_distill_tasks",
    "get_card_reports_grouped",
    "get_comment_reports_grouped",
    "get_config_changelog",
    "get_dashboard_stats",
    "get_review_logs",
    "get_usage_quality_stats",
    "list_all_cards_admin",
    "list_all_posts_admin",
    "list_distill_tasks",
)


def _consts(node) -> list[str]:
    """方法体里的 SQL 片段（常数串 + f-string 的字面部分）。"""
    out = []
    for s in ast.walk(node):
        if isinstance(s, ast.Constant) and isinstance(s.value, str):
            out.append(s.value)
        elif isinstance(s, ast.JoinedStr):
            out += [v.value for v in s.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return out


def _owner_table_idcols() -> dict[str, str]:
    """属主表 -> 连回 users 的身份列。DDL 真源：storage/migrations_pg/*.sql。"""
    tabs: dict[str, list[str]] = {}
    pg_dir = REPO_ROOT / "storage" / "migrations_pg"
    for path in sorted(pg_dir.glob("*.sql")):
        s = _RE_COMMENT.sub("", path.read_text(encoding="utf-8"))
        for m in _RE_CREATE.finditer(s):
            t = m.group("t").lower()
            for line in m.group("b").split(","):
                cm = _RE_COLNAME.match(line)
                if cm:
                    tabs.setdefault(t, []).append(cm.group("c").lower())
        for m in _RE_ALTER.finditer(s):
            tabs.setdefault(m.group("t").lower(), []).append(m.group("c").lower())
    names = set(tabs)
    out: dict[str, str] = {}
    for t, cols in tabs.items():
        for c in cols:
            if not c.endswith("_id"):
                continue
            pre = c[:-3]
            if pre in _ID_ALIAS or pre in names:
                out[t] = c
                break
    return out


def _where_has_idcol(stmt: str, idcols: set[str]) -> bool:
    """只在 WHERE 子句里找身份列谓词 —— JOIN ... ON 不算过滤（只配对，不筛行）。"""
    m = re.search(r"\bWHERE\b", stmt, re.I)
    if not m:
        return False
    tail = stmt[m.end():]
    cut = re.search(r"\b(?:GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b", tail, re.I)
    if cut:
        tail = tail[:cut.start()]
    return any(re.search(rf"\b{re.escape(c)}\b\s*(?:=|<>|!=|IN\b|LIKE\b|IS\b)", tail, re.I) for c in idcols)


def _owner_read_primitives() -> dict[str, list[str]]:
    """{原语名 -> 读到的属主表}：读属主表 ∧ WHERE 无身份列谓词 ∧ 无身份参数 ∧ 非 `_unscoped`。

    两后端取并集，判据与代码路径同 `_scan_class` —— 同名原语某一边漏了身份谓词即现形。
    """
    idc = _owner_table_idcols()
    flagged: dict[str, set[str]] = {}
    for rel, cls_name in _BACKENDS:
        for name, tables in _scan_class(REPO_ROOT / rel, cls_name, idc).items():
            flagged.setdefault(name, set()).update(tables)
    return {name: sorted(tables) for name, tables in flagged.items()}


def _scan_class(path: pathlib.Path, cls_name: str, idc: dict[str, str]) -> dict[str, list[str]]:
    """扫一个后端类，返回该类的 {原语名 -> 读到的属主表}（判据同 `_owner_read_primitives`）。"""
    owner_tables = set(idc)
    tree = _parse(path)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    flagged: dict[str, list[str]] = {}
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name.startswith("_"):
            continue
        if fn.name.endswith("_unscoped"):
            continue
        sql = "\n".join(_consts(fn))
        if not _RE_SELECT.search(sql) or _RE_WRITE.search(sql):
            continue
        args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        idp = bool(set(args) & _ID_PARAMS)
        reads = {m.group("t").lower() for m in _RE_FROM.finditer(sql)}
        owner_reads = reads & owner_tables
        if not owner_reads:
            continue
        if idp or any(_where_has_idcol(s, {idc[t] for t in owner_reads}) for s in _consts(fn)):
            continue
        flagged[fn.name] = sorted(owner_reads)
    return flagged


def _named_call_sites(names: set[str], *, skip_tests: bool) -> dict[str, set[str]]:
    """{原语名 -> 命中它的文件相对路径}。skip_tests 时排除 tests/（测试无登录语境）。"""
    out: dict[str, set[str]] = {n: set() for n in names}
    for root_name in SCAN_ROOTS:
        if skip_tests and root_name == "tests":
            continue
        base = REPO_ROOT / root_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in PRUNE_DIRS for part in path.parts):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if skip_tests and rel.startswith("tests/"):
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in names):
                    out[node.func.attr].add(rel)
    return out


def test_no_owner_table_read_without_identity_or_declaration():
    flagged = _owner_read_primitives()
    undeclared = {n: t for n, t in flagged.items() if n not in OWNER_READ_ALLOWLIST}
    assert undeclared == {}, (
        f"以下公开读原语读了属主表，却既没有身份参数、WHERE 也没按身份列收窄，且未登记豁免："
        f"{sorted(undeclared)}（表={undeclared}）。"
        "改用/新增 *_owned（SQL 里 WHERE <idcol> = ?）；确属有意（公开面/管理面/凭据路径/"
        "identity_primitive）才加进 OWNER_READ_ALLOWLIST 并写明理由。"
    )


def test_owner_read_allowlist_has_no_stale_entries():
    stale = set(OWNER_READ_ALLOWLIST) - set(_owner_read_primitives())
    assert stale == set(), (
        f"OWNER_READ_ALLOWLIST 里的条目已不再命中（已收窄 / 改名 / 已删），请删除：{sorted(stale)}"
    )


def test_admin_management_primitives_are_called_only_from_admin():
    sites = _named_call_sites(set(ADMIN_MANAGEMENT_PRIMITIVES), skip_tests=True)
    vacuous = [n for n in ADMIN_MANAGEMENT_PRIMITIVES if not sites[n]]
    assert vacuous == [], (
        f"这些管理原语没有任何生产调用点，断言对其恒真（空转 = 假绿）：{vacuous}。"
        "要么它是死代码该删（先例 get_comment_reports），要么它不该留在这个集合里。"
    )
    stray = {n: sorted(s) for n, s in sites.items() if s != {"web/routers/admin.py"}}
    assert stray == {}, (
        f"这些管理原语的调用点跑到 admin.py 之外了：{stray}。"
        "它们读全站属主数据，「跨属主」的前提是只有管理员路径能到 —— "
        "要么把调用点收回 admin.py，要么给该原语补身份收窄（只读某个登录用户的子集）。"
    )


def test_scan_coverage_pg_has_no_read_primitive_absent_from_sqlite():
    """两后端公开方法集必须镜像（PG 只多一个 `close`）。

    SQL 事实锁现在已经**同时扫两后端**（`_BACKENDS`），所以 PG 独有的读原语不再靠这条兜 ——
    这条守的是另一件事：**方法集镜像**本身。锁只关心「读属主表且未收窄」的方法，PG 独有的
    写方法 / 不读属主表的方法它看不见；两后端漂移出这类方法时由这条红。"""
    def _public(path: pathlib.Path, cls_name: str) -> set[str]:
        tree = _parse(path)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        return {f.name for f in cls.body
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and not f.name.startswith("_")}

    sqlite_pub = _public(REPO_ROOT / _BACKENDS[0][0], _BACKENDS[0][1])
    pg_only = _public(REPO_ROOT / _BACKENDS[1][0], _BACKENDS[1][1]) - sqlite_pub
    assert pg_only <= {"close"}, (
        f"postgres_store 有 sqlite 没有的公开方法：{sorted(pg_only - {'close'})}。"
        "两后端应镜像（同一 StorageBase 契约）—— 按 sqlite 侧同形补齐；"
        "若它是 PG 独有的读原语，还要确认 SQL 事实锁扫得到（锁已同时扫两后端）。"
    )
