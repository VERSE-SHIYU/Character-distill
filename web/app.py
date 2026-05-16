"""Gradio 角色模拟器：蒸馏角色卡并与角色沉浸式对话。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import gradio as gr
import yaml

_CFG_PATH = _REPO_ROOT / "config.yaml"

try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        cfg: dict[str, Any] = yaml.safe_load(_f)
except OSError as exc:
    print(f"读取配置文件失败：{_CFG_PATH}，原因：{exc}")
    raise
except yaml.YAMLError as exc:
    print(f"解析 YAML 失败：{_CFG_PATH}，原因：{exc}")
    raise

# 延迟初始化：LLM / Distiller 在首次蒸馏时才创建，避免阻塞 Gradio 启动
_llm = None
_distiller = None


def _get_llm_and_distiller():
    """懒加载 LLMAdapter 与 Distiller（仅第一次调用时初始化）。"""
    global _llm, _distiller
    if _llm is None:
        from adapters.llm_adapter import LLMAdapter
        from core.distiller import Distiller
        try:
            _llm = LLMAdapter()
            _distiller = Distiller(_llm)
        except Exception as exc:
            print(f"初始化 LLM / Distiller 失败：{exc}")
            raise gr.Error(f"初始化 LLM 失败：{exc}") from exc
    return _llm, _distiller


def run_distill(text: str, file: Any, char_name: str) -> tuple[str, Any, Any, str]:
    """蒸馏按钮回调，返回角色卡 Markdown、状态对象与状态文案。"""
    from core.chat_engine import ChatEngine
    from core.rag import RAGEngine
    from core.schema import CharacterCard

    try:
        llm, distiller = _get_llm_and_distiller()
        raw_text = (text or "").strip()

        if not raw_text:
            if file is None:
                raise gr.Error("请输入文本或上传文件")
            try:
                path = file if isinstance(file, str) else getattr(file, "name", str(file))
                with open(path, encoding="utf-8") as fp:
                    raw_text = fp.read().strip()
            except OSError as exc:
                print(f"读取上传文件失败：{exc}")
                raise gr.Error(f"读取上传文件失败：{exc}") from exc

        if not raw_text:
            raise gr.Error("请输入文本或上传文件")

        chosen_name = char_name.strip()
        if chosen_name:
            card = distiller.distill(raw_text, chosen_name)
        else:
            try:
                chars = distiller.identify_characters(raw_text)
            except Exception as exc:
                print(f"自动识别角色失败：{exc}")
                raise gr.Error(f"自动识别角色失败：{exc}") from exc
            if not chars:
                raise gr.Error("未能识别到主角，请在「角色名」中手动填写")
            try:
                protagonist = chars[0]["name"]
            except (KeyError, TypeError) as exc:
                print(f"识别结果缺少 name 字段：{chars[0]!r}")
                raise gr.Error("识别结果格式不正确，请手动填写角色名") from exc
            card = distiller.distill(raw_text, str(protagonist))

        try:
            rag = RAGEngine(cfg["rag"])
            rag.index(raw_text)
            engine = ChatEngine(llm, rag, card)
        except KeyError as exc:
            print(f"配置缺少 rag 段落：{exc}")
            raise gr.Error("配置缺少 rag 段落，请检查 config.yaml") from exc
        except Exception as exc:
            print(f"初始化 RAG / ChatEngine 失败：{exc}")
            raise gr.Error(f"初始化检索或对话引擎失败：{exc}") from exc

        card_md = (
            f"## {card.name}\n"
            f"**{card.identity}**\n\n"
            "| 维度 | 内容 |\n"
            "|------|------|\n"
            f"| 性格 | {'; '.join(card.personality_traits)} |\n"
            f"| 口癖 | {'; '.join(card.speaking_style.catchphrases)} |\n"
            f"| 语气 | {card.speaking_style.tone} |\n"
            f"| 价值观 | {'; '.join(card.values)} |\n"
            f"| 内在矛盾 | {'; '.join(card.inner_tensions)} |\n\n"
            f"**背景**：{card.background}\n"
        )

        status_md = f"✅ 当前角色：{card.name} — {card.identity}"
        return card_md, card, engine, status_md
    except gr.Error:
        raise
    except Exception as exc:
        print(f"蒸馏流程出现异常：{exc}")
        raise gr.Error(str(exc)) from exc


def respond(message: str, history: list | None, engine: Any) -> tuple[list, Any]:
    """聊天回调：调用 ChatEngine 并同步更新 Chatbot 消息列表。"""
    try:
        if engine is None:
            raise gr.Error("请先蒸馏角色")

        hist = list(history or [])
        stripped = (message or "").strip()
        if not stripped:
            raise gr.Error("请输入内容后再发送")

        try:
            resp, _ = engine.chat(stripped)
        except Exception as exc:
            print(f"对话调用失败：{exc}")
            raise gr.Error(f"对话调用失败：{exc}") from exc

        hist.append({"role": "user", "content": stripped})
        hist.append({"role": "assistant", "content": resp})
        return hist, engine
    except gr.Error:
        raise
    except Exception as exc:
        print(f"聊天回调出现异常：{exc}")
        raise gr.Error(str(exc)) from exc


def clear_conversation(engine: Any) -> list:
    """清空 ChatEngine 内部历史并刷新聊天界面。"""
    if engine is None:
        return []
    try:
        engine.reset()
    except Exception as exc:
        print(f"清空对话历史失败：{exc}")
        raise gr.Error(f"清空对话历史失败：{exc}") from exc
    return []


def reset_character_session() -> tuple[Any, Any, list, str]:
    """丢弃当前角色与会话状态，回到初始提示。"""
    return None, None, [], "⚠️ 请先在「蒸馏角色」页完成蒸馏"


def clear_message_box() -> str:
    """发送后清空输入框。"""
    return ""


with gr.Blocks(title="角色模拟器") as app:
    gr.Markdown("# 📖 角色模拟器")

    card_state = gr.State(None)
    engine_state = gr.State(None)

    with gr.Tabs():
        with gr.Tab("🧬 蒸馏角色"):
            txt_input = gr.Textbox(lines=12, label="粘贴文本", placeholder="把文字贴在这里...")
            file_input = gr.File(file_types=[".txt"], label="或上传 .txt 文件")
            name_input = gr.Textbox(label="角色名（不填则自动识别主角）", placeholder="如：张三")
            distill_btn = gr.Button("🔍 开始蒸馏", variant="primary", size="lg")
            card_output = gr.Markdown(label="角色卡")

        with gr.Tab("💬 沉浸对话"):
            status_md = gr.Markdown("⚠️ 请先在「蒸馏角色」页完成蒸馏")
            chatbot = gr.Chatbot(height=500)
            msg_input = gr.Textbox(placeholder="说点什么...", submit_btn="发送")
            with gr.Row():
                clear_btn = gr.Button("🗑️ 清空对话")
                reset_btn = gr.Button("🔄 换个角色")

    distill_btn.click(
        fn=run_distill,
        inputs=[txt_input, file_input, name_input],
        outputs=[card_output, card_state, engine_state, status_md],
    )

    msg_input.submit(
        fn=respond,
        inputs=[msg_input, chatbot, engine_state],
        outputs=[chatbot, engine_state],
    ).then(
        fn=clear_message_box,
        inputs=None,
        outputs=msg_input,
    )

    clear_btn.click(
        fn=clear_conversation,
        inputs=[engine_state],
        outputs=[chatbot],
    )

    reset_btn.click(
        fn=reset_character_session,
        outputs=[card_state, engine_state, chatbot, status_md],
    )


if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())
