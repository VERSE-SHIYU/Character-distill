#!/usr/bin/env python3
"""诊断 deepseek-v4-pro 的 function-calling 兼容性。

4 组对照，全部走项目的 LLMAdapter / ChatEngine 路径，不改生产代码。

用法:
  python scripts/diag_tool_use.py --card_id test_card_9c3dc88d
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.llm_adapter import LLMAdapter
from core.chat_engine import ChatEngine
from core.agent.tools import AgentToolkit
from core.schema import CharacterCard
from storage import get_store
import asyncio


def pprint(label: str, content: str, tool_calls: list | None) -> None:
    """统一格式打印一组结果。"""
    print(f"{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  content:   {content[:300]!r}")
    if tool_calls:
        print(f"  tool_calls: {len(tool_calls)}")
        for tc in tool_calls:
            fn = tc.function
            print(f"    [{tc.id[:8]}...] {fn.name}({fn.arguments})")
    else:
        print(f"  tool_calls: (none)")
    print()


async def load_card(card_id: str) -> CharacterCard:
    storage = get_store()
    rec = await storage.get_card(card_id)
    if not rec:
        print(f"Card {card_id} not found")
        sys.exit(1)
    return CharacterCard.model_validate_json(rec["card_json"])


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--card_id", default="test_card_9c3dc88d")
    args = parser.parse_args()

    llm = LLMAdapter()
    card = asyncio.run(load_card(args.card_id))
    print(f"Model: {llm.model}  Base: {llm._base_url}\n")

    # ── 构建真实角色 system prompt（评测场景的产物） ──
    engine = ChatEngine(llm=llm, rag=None, card=card, card_id="diag")
    real_sp = engine._compose_system_prompt(
        "今天是几月几号？", voice_mode=False, include_dynamic=False,
    )
    tool_sp = engine._compose_system_prompt(
        "今天是几月几号？", voice_mode=False, include_dynamic=True,
    )

    # ── 三工具 schema ──
    toolkit = AgentToolkit(engine._ctx_engine)
    schemas = toolkit.get_schemas()

    # ── 统一 input ──
    question = "今天是几月几号？"
    messages = [{"role": "user", "content": question}]

    # ── A: 极简 system + 三工具（模型自主决策） ──
    A_SYSTEM = "你是一个助手，可以调用工具获取信息。"
    msg_a = llm.chat_with_tools(A_SYSTEM, messages, schemas)
    pprint("A: 极简 system + 自主决策", msg_a.content or "", msg_a.tool_calls)

    # ── B: tool_choice 强制 ──
    # 直接走 adapter._client（底层 OpenAI SDK）加 tool_choice
    try:
        resp = llm._client.chat.completions.create(
            model=llm.model,
            messages=[{"role": "system", "content": A_SYSTEM}, *messages],
            tools=schemas,
            tool_choice={"type": "function", "function": {"name": "web_search"}},
            temperature=llm._temperature,
            max_tokens=256,
        )
        msg_b = resp.choices[0].message
        pprint("B: 极简 system + tool_choice=web_search (强制)", msg_b.content or "", msg_b.tool_calls)
    except Exception as e:
        print(f"{'='*60}")
        print(f"  B: 极简 system + tool_choice=web_search (强制)")
        print(f"{'='*60}")
        print(f"  ERROR: {e}")
        print()

    # ── C: 真实角色 system prompt + 三工具（模型自主决策） ──
    msg_c = llm.chat_with_tools(real_sp, messages, schemas)
    pprint("C: 真实角色 prompt + 自主决策", msg_c.content or "", msg_c.tool_calls)

    # ── D: 真实角色 prompt + 末尾追加工具指引 ──
    D_APPEND = (
        "\n【工具】你可以调用工具检索记忆、场景或联网信息。"
        "当对话需要你不知道的事实、或对方提及过往交流时，先调用相应工具再回答。"
        "调用工具不会打断角色扮演。"
    )
    d_sp = real_sp + D_APPEND
    msg_d = llm.chat_with_tools(d_sp, messages, schemas)
    pprint("D: 真实角色 prompt + 工具指引", msg_d.content or "", msg_d.tool_calls)


if __name__ == "__main__":
    main()
