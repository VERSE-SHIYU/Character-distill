"""基于 OpenAI SDK 的 DeepSeek 兼容接口封装。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import os

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI, BadRequestError, OpenAI


def _classify_retry(exc: Exception) -> tuple[bool, float | None]:
    """返回 (是否为429限流, Retry-After秒数或None)。"""
    is_429 = False
    # openai.RateLimitError may not be importable everywhere, check by name
    if type(exc).__name__ == "RateLimitError" and "openai" in type(exc).__module__:
        is_429 = True
    else:
        is_429 = getattr(exc, "status_code", None) == 429 or "429" in str(exc)

    retry_after = None
    if is_429:
        try:
            headers = getattr(exc, "response", None).headers if hasattr(exc, "response") else None
            if headers:
                val = headers.get("Retry-After", "")
                if val and val.isdigit():
                    retry_after = min(float(val), 60.0)
        except Exception:
            pass
    return is_429, retry_after


def _backoff_delay(attempt: int, is_rate_limit: bool, retry_after: float | None) -> float:
    """Calculate backoff delay in seconds.

    * With *retry-after*: retry_after + small jitter.
    * 429 without retry-after: exponential backoff min(2**attempt*2, 30) + jitter.
    * Non-429: linear (attempt+1)*5 (unchanged from original).
    """
    if retry_after is not None:
        return retry_after + random.uniform(0, 1)
    if is_rate_limit:
        return min(2 ** attempt * 2, 30.0) + random.uniform(0, 2)
    return float((attempt + 1) * 5)


class ToolsNotSupportedError(RuntimeError):
    """Provider 不支持 tools 参数时抛出，上层据此降级到 legacy 路径。"""


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

        # Load defaults from config.yaml (fallback to config.example.yaml)
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
        if config_path is None and not cfg_file.exists():
            cfg_file = root / "config.example.yaml"
        llm_cfg: dict[str, Any] = {}
        try:
            raw = cfg_file.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if isinstance(data, dict) and "llm" in data:
                llm_cfg = data["llm"]
        except Exception as exc:
            print(f"[LLMAdapter] Config file load failed, using defaults: {exc}")
            pass

        self._base_url = base_url or str(llm_cfg.get("base_url", "https://api.deepseek.com"))
        self._model = model or str(llm_cfg.get("model", "deepseek-v4-pro"))
        self._temperature = temperature if temperature is not None else float(llm_cfg.get("temperature", 0.7))
        self._max_tokens = max_tokens if max_tokens is not None else int(llm_cfg.get("max_tokens", 4096))
        self._presence_penalty = float(llm_cfg.get("presence_penalty", 0.3))
        self.last_usage: dict | None = None

        resolved_key = api_key or llm_cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        self._api_key = resolved_key

        if not resolved_key:
            raise RuntimeError("missing API key — configure in Settings or set DEEPSEEK_API_KEY")

        try:
            self._client = OpenAI(api_key=resolved_key, base_url=self._base_url, timeout=600.0)
            self._async_client = AsyncOpenAI(api_key=resolved_key, base_url=self._base_url, timeout=600.0)
        except Exception as exc:
            print(f"初始化 OpenAI 客户端失败：{exc}")
            raise

    def _make_async_client(self) -> AsyncOpenAI:
        """Create a standalone AsyncOpenAI for a single asyncio.run cycle."""
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, timeout=600.0)

    async def aclose(self) -> None:
        """幂等关闭异步客户端。"""
        client = getattr(self, "_async_client", None)
        if client is None:
            return
        self._async_client = None
        try:
            await client.close()
        except Exception:
            pass

    @property
    def model(self) -> str:
        return self._model

    def _build_messages(self, system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """组装包含系统提示的对话消息列表。"""
        return [{"role": "system", "content": system_prompt}, *messages]

    async def achat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """异步非流式对话，返回完整文本回复。最多重试3次。"""
        result, usage = await self.async_chat(system_prompt, messages, max_tokens=max_tokens)
        if usage:
            self.last_usage = usage
        return result

    def chat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        """非流式对话，返回完整文本回复。最多重试3次（非429）或5次（429限流）。"""
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        normal_budget = 3
        rate_limit_budget = 5
        attempt = 0
        last_error = None
        while True:
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
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
                attempt += 1
                last_error = exc
                is_429, retry_after = _classify_retry(exc)

                if is_429:
                    rate_limit_budget -= 1
                else:
                    normal_budget -= 1

                exhausted = (is_429 and rate_limit_budget <= 0) or (not is_429 and normal_budget <= 0)
                if exhausted:
                    if is_429:
                        print(f"[LLMAdapter] Rate limited (429): all {attempt} attempts exhausted")
                        raise RuntimeError(f"LLM API rate limited (429) after {attempt} attempts: {last_error}")
                    print(f"[LLMAdapter] All {attempt} attempts failed: {last_error}")
                    raise RuntimeError(f"LLM API failed after {attempt} attempts: {last_error}")

                wait = _backoff_delay(attempt - 1, is_429, retry_after)
                if is_429:
                    print(f"[LLMAdapter] Rate limited (429), attempt {attempt}, waiting {wait:.1f}s")
                else:
                    print(f"[LLMAdapter] Attempt {attempt} failed: {last_error}, retrying in {wait}s...")
                time.sleep(wait)

    async def async_chat(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> tuple[str, dict | None]:
        """异步非流式对话，用于 Map 阶段并发。最多重试3次（非429）或5次（429限流）。

        Returns ``(result, usage)`` where *usage* is ``{"prompt_tokens": N,
        "completion_tokens": N}`` or *None*.  Callers are responsible for
        aggregating usage across concurrent calls instead of relying on the
        shared ``last_usage`` attribute.  """
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        normal_budget = 3
        rate_limit_budget = 5
        attempt = 0
        last_error = None
        while True:
            try:
                completion = await self._async_client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                result = choices[0].message.content or ""
                usage = None
                if completion.usage:
                    usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return result, usage
            except Exception as exc:
                attempt += 1
                last_error = exc
                is_429, retry_after = _classify_retry(exc)

                if is_429:
                    rate_limit_budget -= 1
                else:
                    normal_budget -= 1

                exhausted = (is_429 and rate_limit_budget <= 0) or (not is_429 and normal_budget <= 0)
                if exhausted:
                    if is_429:
                        print(f"[LLMAdapter async] Rate limited (429): all {attempt} attempts exhausted")
                        raise RuntimeError(f"Async LLM rate limited (429) after {attempt} attempts: {last_error}")
                    print(f"[LLMAdapter async] All {attempt} attempts failed: {last_error}")
                    raise RuntimeError(f"Async LLM failed after {attempt} attempts: {last_error}")

                wait = _backoff_delay(attempt - 1, is_429, retry_after)
                if is_429:
                    print(f"[LLMAdapter async] Rate limited (429), attempt {attempt}, waiting {wait:.1f}s")
                else:
                    print(f"[LLMAdapter async] Attempt {attempt} failed: {last_error}, retrying in {wait}s...")
                await asyncio.sleep(wait)

    def chat_stream(self, system_prompt: str, messages: list[dict[str, Any]], max_tokens: int | None = None) -> Generator[str, None, None]:
        """流式对话，按增量产出文本片段。"""
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        prompt_chars = sum(len(m.get("content", "")) for m in payload)
        self.last_usage = None  # 切断上一轮污染
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                temperature=self._temperature,
                max_tokens=_mt,
                presence_penalty=self._presence_penalty,
                stream=True,
                stream_options={"include_usage": True},
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            print(f"调用 DeepSeek Chat API 失败（流式）：{exc}")
            raise
        completion_chars = 0
        try:
            for chunk in stream:
                if chunk.usage:
                    self.last_usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "estimated": False,
                    }
                    continue
                choices = chunk.choices
                if not choices:
                    continue
                delta = choices[0].delta
                piece = delta.content
                if piece:
                    completion_chars += len(piece)
                    yield piece
            # 厂商全程未回 usage chunk → 字符估算兜底
            if self.last_usage is None:
                self.last_usage = {
                    "prompt_tokens": int(prompt_chars / 1.5),
                    "completion_tokens": int(completion_chars / 1.5),
                    "estimated": True,
                }
                print(f"[llm] usage chunk missing, estimated from chars (pt~{self.last_usage['prompt_tokens']} ct~{self.last_usage['completion_tokens']})")
        except Exception as exc:
            print(f"读取流式响应失败：{exc}")
            raise

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Any:
        """非流式 function-calling 对话，返回完整 message 对象（含 tool_calls）。

        网络/临时错误重试（非429最多3次，429限流最多5次）；
        provider 不支持 tools（400 + tool/function 关键词）→ ToolsNotSupportedError，不重试；
        其他 400 → 原样抛出，不重试。
        """
        payload = self._build_messages(system_prompt, messages)
        _mt = max_tokens if max_tokens is not None else self._max_tokens
        normal_budget = 3
        rate_limit_budget = 5
        attempt = 0
        last_error: Exception | None = None
        while True:
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=payload,
                    tools=tools,
                    temperature=self._temperature,
                    max_tokens=_mt,
                    presence_penalty=self._presence_penalty,
                    extra_body={"enable_thinking": False},
                )
                choices = completion.choices
                if not choices:
                    raise RuntimeError("API returned empty choices")
                msg = choices[0].message
                if completion.usage:
                    self.last_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens or 0,
                        "completion_tokens": completion.usage.completion_tokens or 0,
                    }
                return msg
            except BadRequestError as exc:
                err_text = " ".join(
                    filter(None, [exc.message, str(exc), str(getattr(exc, "body", "") or "")])
                ).lower()
                if "tool" in err_text or "function" in err_text:
                    raise ToolsNotSupportedError(
                        f"Provider does not support tools/function-calling: {exc}"
                    ) from exc
                raise  # 其他 400：不重试，原样抛出
            except Exception as exc:
                attempt += 1
                last_error = exc
                is_429, retry_after = _classify_retry(exc)

                if is_429:
                    rate_limit_budget -= 1
                else:
                    normal_budget -= 1

                exhausted = (is_429 and rate_limit_budget <= 0) or (not is_429 and normal_budget <= 0)
                if exhausted:
                    if is_429:
                        print(f"[LLMAdapter] chat_with_tools: Rate limited (429), all {attempt} attempts exhausted")
                        raise RuntimeError(f"chat_with_tools rate limited (429) after {attempt} attempts: {last_error}")
                    print(f"[LLMAdapter] chat_with_tools: all {attempt} attempts failed: {last_error}")
                    raise RuntimeError(f"chat_with_tools failed after {attempt} attempts: {last_error}")

                wait = _backoff_delay(attempt - 1, is_429, retry_after)
                if is_429:
                    print(f"[LLMAdapter] chat_with_tools: Rate limited (429), attempt {attempt}, waiting {wait:.1f}s")
                else:
                    print(f"[LLMAdapter] chat_with_tools attempt {attempt} failed: {last_error}, retrying in {wait}s...")
                time.sleep(wait)
