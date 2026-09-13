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
