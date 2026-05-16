"""蒸馏引擎：从文本中识别角色并生成结构化角色卡。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from adapters.llm_adapter import LLMAdapter
from core.schema import CharacterCard


IDENTIFY_SYSTEM_PROMPT = (
    '阅读以下文本，找出所有有名字且有对话或行为描写的角色。只返回 JSON 数组，格式：[{"name": "张三", '
    '"importance": "主要", "reason": "出现频率最高的角色"}]。不要返回任何其他内容。'
)

DISTILL_PROMPT_BEFORE_NAME = """你是一个角色分析专家。从给定文本中精确提取角色 \""""

DISTILL_PROMPT_AFTER_NAME = """\" 的完整人格档案。

## 分析铁律
1. 跨场景验证：一个特质必须在至少2个不同场景出现才能写入
2. 有预测力：提取的特质能预测此人在新情境下的反应
3. 保留矛盾：矛盾是真实人格的标志，不准美化、不准调和
4. 忠于原文：他是什么样就是什么样。不添加、不美化、不删减

## 分析维度（每个维度必须给出原文证据）

A. 基本信息：名字、身份、背景
B. 核心性格（3-5个）：每个特质 + 原文中的具体场景作为证据
C. 说话风格：语气、句式、口癖（直接从原文对话提取）、用词水平、禁忌用词
D. 价值观（2-4个）：什么对他最重要？两难时怎么选？
E. 关键记忆（3-5个）：塑造此人的重要经历
F. 人际关系：与文中其他角色的关系和态度
G. 内在矛盾（1-3个）：此人身上自相矛盾之处，以及矛盾如何影响行为
H. 开场白：以此角色的口吻写一句开场白，用于对话开始时

## 输出要求
严格按以下 JSON 格式输出，不要输出任何其他内容（不要 markdown 代码块标记）：
"""


class Distiller:
    """基于 LLM 的角色识别与角色卡蒸馏。"""

    def __init__(
        self,
        llm: LLMAdapter,
        config_path: str | Path | None = None,
    ) -> None:
        """初始化蒸馏器。

        Args:
            llm: 已配置好的大模型适配器。
            config_path: 配置文件路径；默认读取仓库根目录 ``config.yaml``。
        """
        self._llm = llm
        root = Path(__file__).resolve().parent.parent
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
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
        if not isinstance(data, dict) or "distill" not in data:
            print("配置文件格式错误：缺少 distill 配置块")
            raise ValueError("invalid config: missing distill section")
        distill_cfg = data["distill"]
        try:
            self._max_input_chars: int = int(distill_cfg["max_input_chars"])
        except (KeyError, TypeError, ValueError) as exc:
            print(f"读取 distill.max_input_chars 失败：{exc}")
            raise

    def identify_characters(self, text: str) -> list[dict[str, Any]]:
        """截取文本前 10000 字，调用 LLM 识别具名且有言行描写的角色。

        Args:
            text: 原始叙事文本。

        Returns:
            角色信息字典列表；解析反复失败时返回空列表并打印警告。
        """
        excerpt = text[:10000]
        messages: list[dict[str, Any]] = [{"role": "user", "content": excerpt}]

        def _parse_list(raw: str) -> list[dict[str, Any]]:
            try:
                parsed = json.loads(raw.strip())
            except json.JSONDecodeError as exc:
                print(f"解析角色识别 JSON 失败：{exc}")
                raise
            if not isinstance(parsed, list):
                print("角色识别结果不是 JSON 数组")
                raise TypeError("expected JSON array")
            out: list[dict[str, Any]] = []
            for idx, item in enumerate(parsed):
                if isinstance(item, dict):
                    out.append(item)
                else:
                    print(f"警告：角色识别数组第 {idx} 项不是对象，已跳过")
            return out

        try:
            reply = self._llm.chat(IDENTIFY_SYSTEM_PROMPT, messages)
        except Exception as exc:
            print(f"调用 LLM 进行角色识别失败：{exc}")
            raise

        try:
            return _parse_list(reply)
        except Exception:
            retry_prompt = IDENTIFY_SYSTEM_PROMPT + "请只返回JSON数组"
            try:
                reply_retry = self._llm.chat(retry_prompt, messages)
            except Exception as exc:
                print(f"角色识别重试调用 LLM 失败：{exc}")
                raise
            try:
                return _parse_list(reply_retry)
            except Exception as exc:
                print(f"警告：角色识别 JSON 经一次重试后仍无法解析，返回空列表。原因：{exc}")
                return []

    def distill(self, text: str, character_name: str) -> CharacterCard:
        """将文本截断后蒸馏指定角色的 ``CharacterCard``。

        Args:
            text: 原始叙事文本。
            character_name: 目标角色姓名。

        Returns:
            校验通过的 ``CharacterCard``。

        Raises:
            ValueError: JSON 经截取与大括号提取后仍无法还原为 ``CharacterCard``。
        """
        truncated = text[: self._max_input_chars]
        try:
            schema_obj = CharacterCard.model_json_schema()
            schema_str = json.dumps(schema_obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            print(f"生成 CharacterCard JSON Schema 失败：{exc}")
            raise

        system_prompt = (
            DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str
        )
        user_messages: list[dict[str, Any]] = [
            {"role": "user", "content": "以下是需要分析的文本：\n\n" + truncated},
        ]

        try:
            reply = self._llm.chat(system_prompt, user_messages)
        except Exception as exc:
            print(f"调用 LLM 进行角色蒸馏失败：{exc}")
            raise

        stripped = reply.strip()
        data: Any | None = None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"蒸馏结果 JSON 解析失败：{exc}，尝试截取首尾大括号之间的片段后重试")
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start == -1 or end == -1 or end <= start:
                print("警告：无法在模型输出中定位有效的 JSON 大括号区间")
                raise ValueError("蒸馏失败：LLM 返回格式不正确") from None
            snippet = stripped[start : end + 1]
            try:
                data = json.loads(snippet)
            except json.JSONDecodeError as exc2:
                print(f"截取大括号后 JSON 仍解析失败：{exc2}")
                raise ValueError("蒸馏失败：LLM 返回格式不正确") from None

        try:
            return CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise ValueError("蒸馏失败：LLM 返回格式不正确") from exc
