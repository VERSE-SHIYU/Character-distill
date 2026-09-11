"""MCP #1 Step 3 自测：路由隔离（构建层）+ 协议错误语义（真实 MCP stdio 往返）。

分两层，均不 mock：
- 用例 (a) 路由隔离：两个不同 card_id 经 `_toolkit_for` 建出的 toolkit 必须是不同
  实例、rag 指向不同 `text_{text_id}` 集合、memory 以不同 card_id 为隔离键——这直接
  证明「按 card_id 路由」生效，**不依赖检索内容**。内容层面「两卡返回不同真实场景」
  需当前 embedder(1024) 索引过的集合才能观测（旧集合是 384 维，query 维度不符被吞成
  空），那是数据/embedder 维度问题（README「行为边界」），不是路由判据。
  注：`_toolkit_by_card_id` 是 server 进程内全局；stdio 客户端是独立进程看不到，故本
  用例在本进程 `import mcp_server.server` 后直接断言（只读构建，等价 Step1 build 验证）。
- 用例 (b)/(c) 协议错误语义：缺/空 card_id、未知 card_id → 协议 isError（绝不是空结果）。
- happy path：一张真实卡经 stdio 调 search_scenes 应非 isError——确认路由改写没弄坏
  线级成功路径。加 `DEMO_REQUIRE_CONTENT=1` 则进一步要求返回**真实原文**：只证
  isError=False 不够，「未找到相关内容」同样满足它，而那正是 characters 过滤静默
  失效的形态。本机 chroma 段错误使有集合的真实检索只能在容器腿跑，故用环境变量显式要求。

运行：
  python mcp_server/client_demo.py
  DEMO_CARD_A=xxx DEMO_CARD_B=yyy python mcp_server/client_demo.py   # 覆盖默认卡
  DEMO_REQUIRE_CONTENT=1 DEMO_CARD_A=14ee2eb526af python mcp_server/client_demo.py   # 容器腿
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PY = Path(__file__).resolve().parent / "server.py"

# 让本进程能 import 仓库内的 core/ 与 mcp_server/（在 client 进程内断言路由隔离）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 默认两卡：均为 testadmin（f46432a6a92e4ae7）数据，不同 text_id —— 安全，仅只读检索
DEFAULT_CARD_A = os.getenv("DEMO_CARD_A", "d50aa3eae638")  # 吴庚霖 text_cd124e88e923（384 旧集合）
DEFAULT_CARD_B = os.getenv("DEMO_CARD_B", "fb975334594d")  # 阿棠 text_3d394865332c（迁移后，尚无集合）

# 1 → happy path 必须返回真实原文（非「未找到相关内容」）。见模块文档串。
REQUIRE_CONTENT = os.getenv("DEMO_REQUIRE_CONTENT") == "1"


def _content_texts(res) -> list[str]:
    out = []
    for c in getattr(res, "content", []) or []:
        if getattr(c, "type", "") == "text":
            out.append(getattr(c, "text"))
    return out


def _is_error(res) -> bool:
    return bool(getattr(res, "isError", False))


async def _case_a_routing_isolation() -> None:
    """用例 (a)：两个不同 card_id 路由到不同 toolkit / rag 引擎 / memory 隔离键。

    不依赖检索内容：router 按 card_id → 该卡 text_id → per-text_id RAGEngine 实例
    （server._rag_by_text_id 缓存），memory 以 ctx.card_id 为 mem0 隔离键。
    RAGEngine.collection_name 只在集合成功 load 后才填充（B 无 1024 集合 → 恒 None），
    故实例隔离才是与数据无关的路由判据；loaded 后两集合名必为 text_{text_id} 不同。
    """
    from mcp_server import server  # import 连带 chromadb —— 仅本用例需要，延迟加载

    st = server._get_storage()
    print(f"[case-a] 路由隔离：card_a={DEFAULT_CARD_A} card_b={DEFAULT_CARD_B}")
    ta = await server._toolkit_for(DEFAULT_CARD_A)
    tb = await server._toolkit_for(DEFAULT_CARD_B)

    distinct = ta is not tb
    print(f"  -> toolkit 不同实例：{distinct}")
    assert distinct, "两卡 toolkit 是同一实例 —— 未按 card_id 隔离"

    tid_a = (await st.get_card(DEFAULT_CARD_A)).get("text_id")
    tid_b = (await st.get_card(DEFAULT_CARD_B)).get("text_id")
    ra_engine, rb_engine = ta._ctx.rag, tb._ctx.rag
    print(f"  -> rag 引擎不同实例：{ra_engine is not rb_engine}  "
          f"target=text_{tid_a} vs text_{tid_b}")
    assert tid_a and tid_b and tid_a != tid_b, "两卡 text_id 相同/缺失 —— 路由目标无法区分"
    assert ra_engine is not None and rb_engine is not None and ra_engine is not rb_engine, \
        "两卡 rag 未隔离到不同 RAGEngine 实例"
    print(f"  -> rag 加载态：A={ra_engine.collection is not None} "
          f"B={rb_engine.collection is not None}（内容检索需 1024 集合，见 README 行为边界）")

    ca_id, cb_id = ta._ctx.card_id, tb._ctx.card_id
    print(f"  -> memory 隔离键(card_id)：A={ca_id!r} B={cb_id!r}")
    assert ca_id == DEFAULT_CARD_A and cb_id == DEFAULT_CARD_B and ca_id != cb_id, "memory 未按 card_id 隔离"

    print("  [PASS] 两 card_id 路由到不同 toolkit / rag 引擎 / memory 隔离键")


async def main() -> None:
    await _case_a_routing_isolation()

    # mcp 1.x stdio 子进程 env 只取 get_default_environment()（白名单），不继承父进程 env。
    # server 需要 STORAGE_BACKEND / DB_PATH / DASHSCOPE_API_KEY 等配置，故必须显式透传。
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_PY)], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"\n[init] server={init.serverInfo.name} v{init.serverInfo.version} "
                  f"protocol={init.protocolVersion}")

            listed = await session.list_tools()
            tools = getattr(listed, "tools", [])
            print(f"[list_tools] {len(tools)} tools")
            for t in tools:
                schema = getattr(t, "inputSchema", {}) or {}
                req = sorted(schema.get("required") or [])
                print(f"  - {t.name}: required={req}")
                assert {"card_id", "query"} <= set(req), f"{t.name} 缺 card_id/query: {req}"

            # ── happy path：真实卡经 stdio 检索，应非 isError（路由改写未弄坏成功路径） ──
            print(f"\n[happy-path] stdio 调 search_scenes card={DEFAULT_CARD_A}")
            r = await session.call_tool(
                "search_scenes", {"card_id": DEFAULT_CARD_A, "query": "角色在书楼里的旧事"}
            )
            c = _content_texts(r)
            print(f"  -> isError={_is_error(r)} content={json.dumps(c, ensure_ascii=False)[:160]}")
            assert not _is_error(r), "真实卡检索不应 isError"
            if REQUIRE_CONTENT:
                from core.agent.tools import EMPTY_RESULT

                assert c and EMPTY_RESULT not in c[0], (
                    f"该卡集合已加载且应为 1024 维，检索却空 —— characters 过滤多半又断了：{c}"
                )
                print("  [PASS] DEMO_REQUIRE_CONTENT=1：search_scenes 返回了真实原文")
            print("  [PASS] 真实卡经 stdio 检索非 isError（内容受 1024 集合数据约束，见 README 行为边界）")

            # ── 用例 (b)：缺 card_id → 协议 isError ──
            print("\n[case-b] 缺 card_id（应 isError，不是空结果）")
            rb_missing = await session.call_tool("search_scenes", {"query": "x"})
            print(f"  -> isError={_is_error(rb_missing)} "
                  f"content={json.dumps(_content_texts(rb_missing), ensure_ascii=False)[:200]}")
            assert _is_error(rb_missing), "缺 card_id 必须 isError"
            # 空字符串 card_id 同样是协议错误
            rb_blank = await session.call_tool("search_scenes", {"card_id": "  ", "query": "x"})
            assert _is_error(rb_blank), "空 card_id 必须 isError"
            print("  [PASS] 缺/空 card_id 均 isError")

            # ── 用例 (c)：未知 card_id → 协议 isError ──
            print("\n[case-c] 未知 card_id（应 isError）")
            rc = await session.call_tool(
                "search_scenes", {"card_id": "no_such_card_xyz", "query": "x"}
            )
            print(f"  -> isError={_is_error(rc)} "
                  f"content={json.dumps(_content_texts(rc), ensure_ascii=False)[:200]}")
            assert _is_error(rc), "未知 card_id 必须 isError"
            print("  [PASS] 未知 card_id isError")


if __name__ == "__main__":
    asyncio.run(main())
    print("STEP3_CLIENT_DEMO_DONE")
