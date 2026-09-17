"""取数工具 —— **不是判据，也不要把它做成锁**。

命题：**请求之外的线程/任务里，有没有一条路能走到 `LLMAdapter` 的出站方法？**
有，那条路就拿不到请求身份（contextvar 里的 client IP），调用点门会 fail-closed
（`LLMCallerMissing`），必须逐处声明 `with system_llm_context():`。

本模块只摆两件**从事实算**的事实，不维护名单：
  1. 全仓的**派生点**（`ctx_thread` / `ctx_submit` / `create_task` / `to_thread`
     / `run_in_executor`），各带所属函数；
  2. `LLMAdapter` 的**出站方法集** —— 从类体现算（公开可调用成员减 `_NON_CALL`）。

**「哪条派生点能到 LLM」不做机器判定** —— 那要跨函数追调用链，判据会退化成一张
手工名单（§四 ③层）。故本模块只摆事实，**归属由人判**，判定结论是下面两张表。

## 一、上下文传播（2026-09-18 实测 + 读标准库源码）

| 派生方式 | OTEL 关 | OTEL 开 | 依据 |
|---|---|---|---|
| `T.ctx_thread` | **不传播** | 传播 | `core/telemetry.py`：关时直接 `threading.Thread(...)` |
| `T.ctx_submit` | **不传播** | 传播 | 同上，关时直接 `pool.submit(...)` |
| `asyncio.create_task` | 传播 | 传播 | 同 context 建 task（标准库行为） |
| `asyncio.to_thread` | 传播 | 传播 | 标准库内部 `contextvars.copy_context()`（本机源码确认） |
| 裸 `loop.run_in_executor` | **不传播** | **不传播** | 只有 `to_thread` 包了 `ctx.run`。`web/server.py` 里 `set_default_executor` 旁那句「`run_in_executor` / `asyncio.to_thread` 自动拷贝 contextvar」**对前者是错的** —— 生产代码零处裸用（只在 `scripts/`），故是注释错、不是缺陷。 |

⇒ 本命题的**唯一断点**是 `T.ctx_thread` / `T.ctx_submit` 在 OTEL 关时的退化路径
（生产默认 OTEL 关）。C2 修的就是它。

## 二、9 处 `ctx_thread` 派生点的归属（人工判定）

| # | 位置（符号名） | 线程体 | 到得出站方法？ | 父来自请求？ | 结论 |
|---|---|---|---|---|---|
| 1-2 | `Distiller` 分片 map（`core/distiller.py`，两处） | 分片并发 | **是**（`_llm.async_chat`） | 是（distill 路由 / 蒸馏后台线程） | 继承，无需声明 |
| 3 | `Distiller` 归并（`core/distiller.py`） | 归并 | **是** | 是 | 继承，无需声明 |
| 4 | `MemoryManager` 入库（`core/memory_manager.py`） | mem0 add | **否**（embedding，不经 adapter） | 是 | 无需声明 |
| 5 | `MemoryManager` 反思（`core/memory_manager.py`） | `llm.chat` | **是** | 是（`reflection_service.maybe_reflect` ← `ChatEngine`） | 继承，无需声明 |
| 6 | `card_guard`（`core/moderation/card_guard.py`） | 卡片审查判词 | **是** | 是（`TextManager` 经 `asyncio.to_thread`） | 继承，无需声明 |
| 7 | `record_usage` 线程（`core/utils.py`） | 落库 | **否**（纯 DB） | 是 | 无需声明 |
| 8 | 蒸馏后台线程（`web/routers/distill.py`） | 整条蒸馏链 | **是** | 是（`/start`） | 继承，无需声明 |
| 9 | 上传解析线程（`web/routers/text.py`） | 解析 + 落库 | **否**（纯 DB；其 `client_ip` 形参已是死参数） | 是 | 无需声明 |

`ctx_submit` 4 处：`Distiller.coref_resolve`（**到得出站**，父为请求）、
`AgentToolkit.execute` 与 `ContextEngine.build_ex` ×2（检索 / embed，不经 adapter）
—— 均继承或无需声明。

`create_task` 各处：`Distiller` ×3（出站）、`group_session` ×2 与 `web/routers/group.py`
×2（`_run_group_affinity` → `evaluation_pipeline` 的 `ctx.llm.chat`，出站）、
`indexing_service`（仅 RAG/embed）、`speech/funasr_server.py`（独立服务）、
`web/server.py` 的 lifespan 两条循环（**均不触达 LLM**）—— 前几类父为请求、天然继承。

**归属结论：生产代码里没有「请求之外」的 LLM 入口。** lifespan 循环、boot reconcile
（`_reconcile_distill_tasks`，纯 DB）、定时任务（全仓无调度器）都不触达 adapter。
`mcp_server/` 与 `scripts/` 是独立进程、不注册守卫，保持现状。
故 `system_llm_context()` 在生产**零调用点** —— 它是给「将来出现请求外入口」留的
唯一正确出口，测试里必须有正控（L7），否则它是一条没人走的死路。

## 用法

`python tests/census_llm_call_contexts.py` 打印读数；pytest 侧取数用裸模块名
`import census_llm_call_contexts`（与 `tests/census_dead_attrs.py` / `route_facts.py`
同款）。本模块**不被任何用例收集**（文件名不以 `test_` 开头），**也不该被断言**。
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

#: 出站方法集 = 公开可调用成员 − 这三个。`model` / `base_url` 是只读事实（属性），
#: `aclose` 是收尾（关客户端）—— 都不该被 geo 门拦。
_NON_CALL = {"model", "aclose", "base_url"}

_SPAWN_KINDS = ("ctx_thread", "ctx_submit", "create_task", "to_thread", "run_in_executor")


@dataclass(frozen=True)
class SpawnSite:
    path: str
    line: int
    kind: str
    owner: str  # "Class.method" / "func" / "<module>"


def _tracked_py() -> list[str]:
    """入库的 .py，去掉 tests/ —— 扫描面只由 git 决定，不靠手工排除表。"""
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_REPO,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return [p for p in out.splitlines() if p and not p.startswith("tests/")]


def _dotted(node: ast.AST) -> str:
    """把 `T.ctx_thread` 还原成点号串；认不出返回空串。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _spawn_kind(call: ast.Call) -> str | None:
    name = _dotted(call.func)
    for kind in _SPAWN_KINDS:
        if name == kind or name.endswith("." + kind):
            return kind
    return None


def _owners(tree: ast.AST) -> dict[int, str]:
    """行号 → 所属 "Class.method" / "func" / "<module>"（最内层）。"""
    out: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                for ln in range(child.lineno, end + 1):
                    out[ln] = qname
                walk(child, f"{qname}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def spawn_sites() -> list[SpawnSite]:
    """全仓派生点（排序稳定，便于两次读数直接 diff）。"""
    sites: list[SpawnSite] = []
    for rel in _tracked_py():
        try:
            tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        owners = _owners(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind = _spawn_kind(node)
            if kind is None:
                continue
            sites.append(SpawnSite(rel, node.lineno, kind, owners.get(node.lineno, "<module>")))
    return sorted(sites, key=lambda s: (s.path, s.line, s.kind))


def outbound_methods() -> list[str]:
    """`LLMAdapter` 的出站方法集 —— 从类体现算，不抄名单。"""
    tree = ast.parse((_REPO / "adapters" / "llm_adapter.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LLMAdapter":
            return sorted(
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
                and n.name not in _NON_CALL
            )
    raise AssertionError("adapters/llm_adapter.py 里找不到 class LLMAdapter")


def main() -> None:
    sites = spawn_sites()
    print(f"扫描 {len(_tracked_py())} 个入库 .py（不含 tests/）")
    print(f"派生点 {len(sites)} 处\n")
    for s in sites:
        print(f"  {s.path:<34} {s.line:>5}  {s.kind:<16} {s.owner}")
    print(f"\nLLMAdapter 出站方法（现算）：{outbound_methods()}")
    print(
        "\n注意：**归属判定不在本模块内** —— 「哪条派生点能到出站方法」「父是不是请求」"
        "要跨函数追链，见模块 docstring 的两张表。此处只摆事实。"
    )


if __name__ == "__main__":
    main()
