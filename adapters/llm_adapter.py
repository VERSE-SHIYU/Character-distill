"""基于 OpenAI SDK 的 DeepSeek 兼容接口封装。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import os

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI


class LLMAdapter:
    """从 ``config.yaml`` 与环境变量加载配置，调用 DeepSeek Chat API。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """初始化适配器并读取配置。

        Args:
            config_path: ``config.yaml`` 路径；默认使用仓库根目录下的 ``config.yaml``。
        """
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
        llm_cfg: dict[str, Any]
        try:
            raw = cfg_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"读取配置文件失败：{cfg_file}，原因：{exc}")
            raise
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            print(f"解析 YAML 失败：{cfg_file}，原因：{exc}")
            raise
        if not isinstance(data, dict) or "llm" not in data:
            print("配置文件格式错误：缺少顶层 llm 配置块")
            raise ValueError("invalid config: missing llm section")
        llm_cfg = data["llm"]
        self._base_url: str = str(llm_cfg["base_url"])
        self._model: str = str(llm_cfg["model"])
        self._temperature: float = float(llm_cfg["temperature"])
        self._max_tokens: int = int(llm_cfg["max_tokens"])

        api_key = llm_cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("未找到 API Key：请在设置页填写，或在 .env 中设置 DEEPSEEK_API_KEY")
            raise RuntimeError("missing API key (config.yaml or DEEPSEEK_API_KEY)")

        try:
            self._client = OpenAI(api_key=api_key, base_url=self._base_url, timeout=300.0)
            self._async_client = AsyncOpenAI(api_key=api_key, base_url=self._base_url, timeout=300.0)
        except Exception as exc:
            print(f"初始化 OpenAI 客户端失败：{exc}")
            raise

    def _build_messages(self, system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """组装包含系统提示的对话消息列表。"""
        return [{"role": "system", "content": system_prompt}, *messages]

    def chat(self, system_prompt: str, messages: list[dict[str, Any]]) -> str:
        """非流式对话，返回完整文本回复。最多重试3次。"""
        payload = self._build_messages(system_prompt, messages)
        last_error = None
        for attempt in range(3):
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    extra_body={"enable_thinking": False},
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                content = choices[0].message.content or ""
                return content
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    wait = (attempt + 1) * 5
                    print(f"[LLMAdapter] Attempt {attempt+1} failed: {exc}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"[LLMAdapter] All 3 attempts failed: {exc}")
        raise RuntimeError(f"LLM API failed after 3 attempts: {last_error}")

    async def async_chat(self, system_prompt: str, messages: list[dict[str, Any]]) -> str:
        """异步非流式对话，用于 Map 阶段并发。最多重试3次。"""
        payload = self._build_messages(system_prompt, messages)
        last_error = None
        for attempt in range(3):
            try:
                completion = await self._async_client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                return choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    wait = (attempt + 1) * 5
                    print(f"[LLMAdapter async] Attempt {attempt+1} failed: {exc}, retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    print(f"[LLMAdapter async] All 3 attempts failed: {exc}")
        raise RuntimeError(f"Async LLM failed after 3 attempts: {last_error}")

    def chat_stream(self, system_prompt: str, messages: list[dict[str, Any]]) -> Generator[str, None, None]:
        """流式对话，按增量产出文本片段。"""
        payload = self._build_messages(system_prompt, messages)
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            print(f"调用 DeepSeek Chat API 失败（流式）：{exc}")
            raise
        try:
            for chunk in stream:
                choices = chunk.choices
                if not choices:
                    continue
                delta = choices[0].delta
                piece = delta.content
                if piece:
                    yield piece
        except Exception as exc:
            print(f"读取流式响应失败：{exc}")
            raise
