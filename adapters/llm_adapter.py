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
    """封装 DeepSeek Chat API 调用。

    支持两种初始化方式：
    1. 显式传参：``LLMAdapter(api_key=..., base_url=..., model=...)`` — 用户配置
    2. 配置文件：``LLMAdapter(config_path=...)`` — 管理员 fallback
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")

        # Load defaults from config.yaml for unspecified params
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
        llm_cfg: dict[str, Any] = {}
        try:
            raw = cfg_file.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if isinstance(data, dict) and "llm" in data:
                llm_cfg = data["llm"]
        except Exception:
            pass

        self._base_url = base_url or str(llm_cfg.get("base_url", "https://api.deepseek.com"))
        self._model = model or str(llm_cfg.get("model", "deepseek-v4-pro"))
        self._temperature = temperature if temperature is not None else float(llm_cfg.get("temperature", 0.7))
        self._max_tokens = max_tokens if max_tokens is not None else int(llm_cfg.get("max_tokens", 4096))
        self.last_usage: dict | None = None

        resolved_key = api_key or llm_cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        if not resolved_key:
            raise RuntimeError("missing API key — configure in Settings or set DEEPSEEK_API_KEY")

        try:
            self._client = OpenAI(api_key=resolved_key, base_url=self._base_url, timeout=600.0)
            self._async_client = AsyncOpenAI(api_key=resolved_key, base_url=self._base_url, timeout=600.0)
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
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
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
                result = choices[0].message.content or ""
                return result
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
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            print(f"调用 DeepSeek Chat API 失败（流式）：{exc}")
            raise
        try:
            for chunk in stream:
                if chunk.usage:
                    self.last_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                    }
                    continue
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
