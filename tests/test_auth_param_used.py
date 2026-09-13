# -*- coding: utf-8 -*-
"""边界锁：端点注入了 get_current_user 却从不引用 user，是一条会重复长出来的缺陷。

text.py:204 / voice.py:189 / voice.py:213 三处都是这个形态，靠人扫 AST 才发现的。
这条测试把「扫 AST」固化下来：将来新增同类端点会自动变红，不用等人再扫一遍。

ALLOWLIST 里的端点是**有意**不需要 user 的（只当登录门，读全局或公开数据）。
新增端点若在名单外又不引用 user，就要显式做决定：用上 user，或加进这里并注明理由。
"""
import ast
import pathlib

ALLOWLIST = {
    "auth.py:get_announcement",     # 全局公告
    "market.py:get_card_forks",     # 公开 fork 列表
    "market.py:list_post_comments", # 公开帖评论
    "voice.py:voice_status",        # 全局服务状态
    "voice.py:speech_to_text",      # 全局 ASR
}


def _is_depends_get_current_user(node) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "Depends"
            and any(isinstance(a, ast.Name) and a.id == "get_current_user" for a in node.args))


def _is_read(fn, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
               for n in ast.walk(fn))


def _unused_user_endpoints() -> set[str]:
    root = pathlib.Path(__file__).resolve().parent.parent
    found: set[str] = set()
    for p in sorted((root / "web" / "routers").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            n_pos = len(fn.args.args) - len(fn.args.defaults)
            params = [
                (a.arg, fn.args.defaults[i - n_pos] if i >= n_pos else None)
                for i, a in enumerate(fn.args.args)
            ]
            params += list(zip([a.arg for a in fn.args.kwonlyargs], fn.args.kw_defaults))
            for name, default in params:
                if _is_depends_get_current_user(default) and not _is_read(fn, name):
                    found.add(f"{p.name}:{fn.name}")
    return found


def test_no_endpoint_injects_user_without_reading_it():
    unexpected = _unused_user_endpoints() - ALLOWLIST
    assert unexpected == set(), (
        f"新出现「注入 user 却不引用」的端点：{sorted(unexpected)}。"
        "要么用上 user（归属校验 / 按用户过滤），要么加进本文件 ALLOWLIST 并注明为何不需要。"
    )


def test_allowlist_has_no_stale_entries():
    """反过来：名单里的端点若已经用上了 user（或已删除），条目就该删掉，别让名单腐烂。"""
    stale = ALLOWLIST - _unused_user_endpoints()
    assert stale == set(), f"ALLOWLIST 里的条目已不再命中，请删除：{sorted(stale)}"
