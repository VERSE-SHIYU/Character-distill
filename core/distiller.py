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
    "阅读以下文本，找出所有有名字且有对话或行为描写的角色。\n"
    "关键要求：如果同一个人有多个称呼（全名、昵称、绰号、姓氏、官职、敬称、代称），"
    "必须归为一组。选最常用的全名作 name，其余放入 aliases。\n"
    '例如：魏无羡/魏婴/夷陵老祖 → name: "魏无羡", aliases: ["魏婴", "夷陵老祖"]\n'
    '例如：汪东城/大东 → name: "汪东城", aliases: ["大东"]\n'
    "\n"
    "只返回 JSON 数组，格式：\n"
    '[{"name": "主名", "aliases": ["别名1", "别名2"], '
    '"importance": "主要/次要", "reason": "简述"}]\n'
    "不要返回任何其他内容。"
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
I. 对话示例（2-3轮）：从原文中提取最能体现此角色说话风格的2-3组对话交互。格式为"对方：xxx\n角色：xxx"。选择的对话必须能展示角色的口癖、语气、态度。如果原文有动作描写，用（）包裹保留，如"（冷笑）你以为你是谁？"
J. 情感模式（2-3个）：什么情况下会生气、开心、沉默、逃避？触发条件是什么？
K. 决策风格：面对选择时是冲动还是谨慎？靠情感还是逻辑？举例说明。

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
        self._chunk_size: int = int(distill_cfg.get("chunk_size", 3000))
        self._max_profile_len: int = int(distill_cfg.get("max_profile_len", 2000))

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
                    if "aliases" not in item:
                        item["aliases"] = []
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

    @staticmethod
    def _split_chunks(text: str, chunk_size: int) -> list[str]:
        """Split text into chunks, with fallback for texts without paragraph breaks."""
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            # 段落本身超长：先按单换行切，再按字符数强制切
            if len(para) > chunk_size:
                if current:
                    chunks.append(current)
                    current = ""
                lines = para.split("\n")
                for line in lines:
                    if len(line) > chunk_size:
                        # 强制按字符数切断
                        for i in range(0, len(line), chunk_size):
                            chunks.append(line[i:i + chunk_size])
                    elif len(current) + len(line) + 1 > chunk_size and current:
                        chunks.append(current)
                        current = line
                    else:
                        current = line if not current else current + "\n" + line
            elif len(current) + len(para) + 2 > chunk_size and current:
                chunks.append(current)
                current = para
            else:
                current = para if not current else current + "\n\n" + para
        if current:
            chunks.append(current)
        return chunks

    def distill(self, text: str, character_name: str) -> CharacterCard:
        """蒸馏指定角色的 ``CharacterCard``（简单截断模式，适合短文本）。

        对于长文本，推荐使用 ``distill_incremental``。
        """
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
            {"role": "user", "content": "以下是需要分析的文本：\n\n" + text[: self._chunk_size * 10]},
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

    def distill_stream(self, text: str, character_name: str):
        """流式蒸馏（简单截断模式，适合短文本）。

        对于长文本，推荐使用 ``distill_incremental_stream``。
        """
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
            {"role": "user", "content": "以下是需要分析的文本：\n\n" + text[: self._chunk_size * 10]},
        ]

        yield from self._llm.chat_stream(system_prompt, user_messages)

    def distill_incremental(
        self,
        text: str,
        character_name: str,
        aliases: list[str] | None = None,
        on_progress: "callable | None" = None,
    ) -> CharacterCard:
        """增量蒸馏：逐块扫描文本，迭代更新角色档案草稿，最终格式化为 CharacterCard。

        每块仅输入当前块 + 已有草稿，LLM 成本接近单次截断。
        """
        aliases = list(aliases) if aliases else []
        match_terms = [character_name] + aliases
        chunks = self._split_chunks(text, self._chunk_size)
        relevant = [c for c in chunks if any(t in c for t in match_terms)]
        if not relevant:
            relevant = chunks[:3]  # fallback: first 3 chunks

        MAX_RELEVANT = 80
        if len(relevant) > MAX_RELEVANT:
            step = len(relevant) / (MAX_RELEVANT - 2)
            sampled = [relevant[0]]
            for i in range(1, MAX_RELEVANT - 1):
                sampled.append(relevant[int(i * step)])
            sampled.append(relevant[-1])
            relevant = sampled

        profile_draft = ""
        for i, chunk in enumerate(relevant):
            if on_progress:
                on_progress(i + 1, len(relevant))

            prompt = (
                f"已有档案：\n{profile_draft}\n\n"
                f"---新片段---\n{chunk}\n---片段结束---\n\n"
                f"请从新片段中提取「{character_name}」的新信息，更新档案。重点关注：\n"
                f"- {character_name}说的原话（完整保留，标注场景）\n"
                f"- 新暴露的性格特质或行为模式\n"
                f"- 与其他角色的互动和态度变化\n"
                f"- 任何与已有档案矛盾的地方"
            ) if profile_draft else (
                f"---新片段---\n{chunk}\n---片段结束---\n\n"
                f"请从新片段中提取「{character_name}」的信息。重点关注：\n"
                f"- {character_name}说的原话（完整保留，标注场景）\n"
                f"- 性格特质和行为模式\n"
                f"- 与其他角色的互动和态度"
            )
            try:
                profile_draft = self._llm.chat(
                    (
                        f"你是角色分析专家，正在为「{character_name}」建立人格档案。\n"
                        "规则：\n"
                        "1. 保留已有档案的全部信息，在此基础上追加新发现\n"
                        "2. 重点提取：说话原文（保留口癖原话）、行为动机、情感反应、价值观冲突\n"
                        "3. 必须保留原文对话原句作为证据，不要改写\n"
                        "4. 发现矛盾不要调和，标注为【矛盾】保留\n"
                        "5. 区分角色本人的话和别人对他的评价"
                    ),
                    [{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                print(f"[distiller] Chunk {i + 1}/{len(relevant)} LLM call failed: {exc}")
                continue

            if len(profile_draft) > self._max_profile_len:
                try:
                    profile_draft = self._llm.chat(
                        (
                        f"压缩以下「{character_name}」的角色档案到{self._max_profile_len}字以内。\n"
                        "优先级：原文对话原句 > 行为证据 > 性格总结 > 背景信息。\n"
                        "口癖和说话风格的原文例句必须保留，这是最重要的。\n"
                        "合并重复信息，但不要删除矛盾点。"
                    ),
                        [{"role": "user", "content": f"请压缩到{self._max_profile_len}字以内：\n\n{profile_draft}"}],
                    )
                except Exception as exc:
                    print(f"[distiller] Profile compression failed: {exc}")

        try:
            schema_obj = CharacterCard.model_json_schema()
            schema_str = json.dumps(schema_obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            print(f"生成 CharacterCard JSON Schema 失败：{exc}")
            raise

        system_prompt = (
            DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str
        )
        try:
            reply = self._llm.chat(
                system_prompt,
                [{"role": "user", "content":
                    f"以下是关于「{character_name}」的完整分析档案，请严格按照JSON格式输出角色卡。\n"
                    f"特别注意：\n"
                    f"- catchphrases 必须是原文中的真实口癖，不要编造\n"
                    f"- dialogue_examples 必须是原文对话，不要改写\n"
                    f"- personality_traits 每条必须附带具体场景证据\n\n"
                    f"{profile_draft}"
                }],
            )
        except Exception as exc:
            print(f"调用 LLM 进行最终格式化失败：{exc}")
            raise

        stripped = reply.strip()
        data: Any | None = None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(stripped[start : end + 1])
                except json.JSONDecodeError:
                    pass

        if data is None:
            raise ValueError("蒸馏失败：LLM 返回格式不正确")

        try:
            return CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise ValueError("蒸馏失败：LLM 返回格式不正确") from exc

    def distill_incremental_stream(
        self,
        text: str,
        character_name: str,
        aliases: list[str] | None = None,
    ):
        """增量蒸馏流式版：逐块处理时 yield 进度事件，最终格式化阶段 yield token。

        yield 值类型：
        - dict: 进度事件或增量阶段的 token（带 phase 标记，不混入最终 card JSON）
        - str: 最终格式化阶段的 token 片段（由 SSE 路由累积为 card JSON）
        """
        aliases = list(aliases) if aliases else []
        match_terms = [character_name] + aliases
        chunks = self._split_chunks(text, self._chunk_size)
        relevant = [c for c in chunks if any(t in c for t in match_terms)]
        if not relevant:
            relevant = chunks[:3]

        MAX_RELEVANT = 80
        if len(relevant) > MAX_RELEVANT:
            step = len(relevant) / (MAX_RELEVANT - 2)
            sampled = [relevant[0]]
            for i in range(1, MAX_RELEVANT - 1):
                sampled.append(relevant[int(i * step)])
            sampled.append(relevant[-1])
            relevant = sampled

        profile_draft = ""
        for i, chunk in enumerate(relevant):
            prompt = (
                f"已有档案：\n{profile_draft}\n\n"
                f"---新片段---\n{chunk}\n---片段结束---\n\n"
                f"请从新片段中提取「{character_name}」的新信息，更新档案。重点关注：\n"
                f"- {character_name}说的原话（完整保留，标注场景）\n"
                f"- 新暴露的性格特质或行为模式\n"
                f"- 与其他角色的互动和态度变化\n"
                f"- 任何与已有档案矛盾的地方"
            ) if profile_draft else (
                f"---新片段---\n{chunk}\n---片段结束---\n\n"
                f"请从新片段中提取「{character_name}」的信息。重点关注：\n"
                f"- {character_name}说的原话（完整保留，标注场景）\n"
                f"- 性格特质和行为模式\n"
                f"- 与其他角色的互动和态度"
            )
            new_draft = ""
            try:
                for token in self._llm.chat_stream(
                    (
                        f"你是角色分析专家，正在为「{character_name}」建立人格档案。\n"
                        "规则：\n"
                        "1. 保留已有档案的全部信息，在此基础上追加新发现\n"
                        "2. 重点提取：说话原文（保留口癖原话）、行为动机、情感反应、价值观冲突\n"
                        "3. 必须保留原文对话原句作为证据，不要改写\n"
                        "4. 发现矛盾不要调和，标注为【矛盾】保留\n"
                        "5. 区分角色本人的话和别人对他的评价"
                    ),
                    [{"role": "user", "content": prompt}],
                ):
                    new_draft += token
                    yield {"status": "analyzing", "current": i + 1, "total": len(relevant), "token": token}
            except Exception as exc:
                print(f"[distiller] Chunk {i + 1}/{len(relevant)} LLM call failed: {exc}")
                continue
            if new_draft.strip():
                profile_draft = new_draft

            if len(profile_draft) > self._max_profile_len:
                compressed = ""
                try:
                    for token in self._llm.chat_stream(
                        (
                        f"压缩以下「{character_name}」的角色档案到{self._max_profile_len}字以内。\n"
                        "优先级：原文对话原句 > 行为证据 > 性格总结 > 背景信息。\n"
                        "口癖和说话风格的原文例句必须保留，这是最重要的。\n"
                        "合并重复信息，但不要删除矛盾点。"
                    ),
                        [{"role": "user", "content": f"请压缩到{self._max_profile_len}字以内：\n\n{profile_draft}"}],
                    ):
                        compressed += token
                        yield {"status": "compressing", "current": i + 1, "total": len(relevant), "token": token}
                except Exception as exc:
                    print(f"[distiller] Profile compression failed: {exc}")
                else:
                    profile_draft = compressed

        try:
            schema_obj = CharacterCard.model_json_schema()
            schema_str = json.dumps(schema_obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            print(f"生成 CharacterCard JSON Schema 失败：{exc}")
            raise

        system_prompt = (
            DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str
        )
        yield from self._llm.chat_stream(
            system_prompt,
            [{"role": "user", "content":
                f"以下是关于「{character_name}」的完整分析档案，请严格按照JSON格式输出角色卡。\n"
                f"特别注意：\n"
                f"- catchphrases 必须是原文中的真实口癖，不要编造\n"
                f"- dialogue_examples 必须是原文对话，不要改写\n"
                f"- personality_traits 每条必须附带具体场景证据\n\n"
                f"{profile_draft}"
            }],
        )
