"""蒸馏引擎：从文本中识别角色并生成结构化角色卡。"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any
import collections.abc as _cabc

import yaml
from pydantic import ValidationError

from adapters.llm_adapter import LLMAdapter
from core.chat_preprocessor import ChatPreprocessor
from core.schema import CharacterCard, PRESET_TAGS
from core.utils import try_record_usage


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
L. 角色弧线：此人从故事开始到结束经历了怎样的变化？分2-4个阶段描述，每阶段一句话。如果无明显变化则写"无明显变化"。

## 输出要求
严格按以下 JSON 格式输出，不要输出任何其他内容（不要 markdown 代码块标记）：
"""


class Distiller:
    """基于 LLM 的角色识别与角色卡蒸馏。"""

    SAFE_SINGLE_REDUCE = 80
    MAP_CONCURRENCY = 30

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
        self._storage = None
        self._user_id: str = ""
        root = Path(__file__).resolve().parent.parent
        cfg_file = Path(config_path) if config_path is not None else root / "config.yaml"
        if config_path is None and not cfg_file.exists():
            cfg_file = root / "config.example.yaml"
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

    def _try_record_usage(self, action: str = "distill", usage: dict | None = None) -> None:
        try_record_usage(
            storage=self._storage,
            user_id=self._user_id,
            llm=self._llm,
            action=action,
            usage=usage,
            source="Distiller",
        )

    # ── static prompt helpers ──────────────────────────────────────────

    @staticmethod
    def _map_system_prompt(character_name: str) -> str:
        return (
            f"你是角色分析专家，正在为「{character_name}」收集人格证据。\n"
            "规则：\n"
            "1. 只提取此片段中的事实，不要推断、不要总结其他片段\n"
            "2. 原文对话原句必须完整保留，这是最重要的\n"
            "3. 矛盾不要调和，标注为【矛盾】并都保留\n"
            "4. 区分角色本人的话与他人评价\n"
            "5. 如果此片段没有该角色的任何信息，回答「无」"
        )

    @staticmethod
    def _map_user_prompt(chunk: str, character_name: str) -> str:
        return (
            f"---文本片段---\n{chunk}\n---片段结束---\n\n"
            f"请从此片段中提取关于「{character_name}」的所有信息：\n"
            f"- {character_name}说的原话（完整保留，标注场景）\n"
            f"- 性格特质和行为模式（必须有原文证据）\n"
            f"- 与其他角色的互动和态度\n"
            f"- 情感反应和价值观体现\n"
            f"如该片段无{character_name}相关信息，输出「无」即可。"
        )

    @staticmethod
    def _map_system_prompt_chat(character_name: str) -> str:
        return (
            f"你是对话分析专家，正在从聊天记录中提取「{character_name}」的说话风格和人格特征。\n"
            "聊天记录格式为：[日期] 发言人: 内容，也可能无日期前缀。\n"
            "规则：\n"
            "1. 只提取此片段中的事实，不要推断\n"
            f"2. 重点关注{character_name}的说话方式、态度、情感反应\n"
            "3. 原文对话必须完整保留，这是最重要的\n"
            f"4. 如果此片段没有{character_name}的发言，输出「无」"
        )

    @staticmethod
    def _map_user_prompt_chat(chunk: str, character_name: str) -> str:
        return (
            f"---聊天记录片段---\n{chunk}\n---片段结束---\n\n"
            f"请提取「{character_name}」在此片段中的表现：\n"
            f"- 说话习惯：口头禅、语气词、句式结构（必须有原文例证）\n"
            f"- 态度：对什么人、什么事表现出什么态度\n"
            f"- 情感反应：什么话题/事件触发了什么情绪反应\n"
            f"- 人际关系：与对话中各参与者的互动模式\n"
            f"- 说话风格：用词水平、句子长短、是否爱用反问/感叹\n"
            f"如该片段无{character_name}发言，输出「无」。"
        )

    @staticmethod
    def _reduce_system_prompt(character_name: str) -> str:
        return (
            f"你正在整合关于「{character_name}」的多份独立片段分析。\n"
            "规则：\n"
            "1. 合并重复信息，但保留所有原文对话原句\n"
            "2. 矛盾不要调和，标注为【矛盾】并都保留\n"
            "3. 区分角色本人的话与他人评价\n"
            "4. 控制在 4000 字以内，原文对话和口癖优先"
        )

    @staticmethod
    def _reduce_user_prompt(analyses: list[str], character_name: str) -> str:
        return (
            f"以下是从多段文本中提取的关于「{character_name}」的独立分析，请整合为一份完整的角色档案：\n\n"
            + "\n\n---片段分隔---\n\n".join(
                f"[来源片段 {i + 1}]\n{a}" for i, a in enumerate(analyses)
            )
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        """剥掉 markdown 代码块，提取第一个完整 JSON 对象。"""
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start:end + 1]
        return text

    # ── public entry points (unchanged) ────────────────────────────────

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

    def coref_resolve(
        self, text: str, characters: list[dict[str, Any]], chunk_size: int = 6000, overlap: int = 500,
        progress_callback: collections.abc.Callable[[int, int], object] | None = None,
    ) -> str:
        """全文共指消解+说话人补全。

        将文本分chunk（带重叠），对每个chunk调LLM替换代词/昵称/省略为角色名，
        并为省略说话人的对话补全说话人标记。

        Args:
            text: 原始全文。
            characters: identify_characters返回的角色列表（含aliases）。
            chunk_size: 每chunk字符数。
            overlap: chunk间重叠字符数。

        Returns:
            共指消解后的全文。
        """
        import asyncio

        alias_lines = []
        for c in characters:
            name = c.get("name", "")
            aliases = c.get("aliases", [])
            if name:
                if aliases:
                    alias_lines.append(f"  {name} → 别名：{'、'.join(aliases)}")
                else:
                    alias_lines.append(f"  {name}")
        alias_table = "\n".join(alias_lines) if alias_lines else "（无角色信息）"

        system_prompt = (
            "你是共指消解专家。对以下文本做两件事：\n"
            "1. 将所有代词（他、她、我、你等）和别名/昵称替换为角色全名。\n"
            "2. 为省略说话人的对话补全说话人标记（如 道：'...' 改为 某某道：'...'）。\n\n"
            "角色及别名：\n" + alias_table + "\n\n"
            "规则：\n"
            "- 只替换能确定指代对象的。不确定的保持原样。\n"
            "- 保持原文的段落结构、标点、格式完全不变。\n"
            "- 不要添加、删除或改写任何内容，只做替换。\n"
            "- 直接输出替换后的文本，不要任何解释或前缀。"
        )

        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append((start, end, text[start:end]))
            if end >= len(text):
                break
            start = end - overlap

        async def _resolve_chunk(chunk_text: str) -> str:
            result, _ = await self._llm.async_chat(
                system_prompt,
                [{"role": "user", "content": chunk_text}],
            )
            return result

        async def _resolve_all():
            total = len(chunks)
            completed = 0

            async def _tracked(chunk_text: str) -> str:
                nonlocal completed
                result = await _resolve_chunk(chunk_text)
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)
                return result

            tasks = [_tracked(c[2]) for c in chunks]
            return await asyncio.gather(*tasks)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                results = pool.submit(lambda: asyncio.run(_resolve_all())).result()
        else:
            results = asyncio.run(_resolve_all())

        if len(results) == 1:
            return results[0]

        final = results[0][:chunk_size - overlap]
        for i in range(1, len(results)):
            if i < len(results) - 1:
                final += results[i][overlap:chunk_size - overlap]
            else:
                final += results[i][overlap:]

        return final

    @staticmethod
    def _split_chunks_chat(text: str, chunk_size: int) -> list[str]:
        """Split chat logs by date, ensuring Q&A pairs stay intact.

        Lines are first grouped by date (each day = one chunk). If a day's
        content exceeds ``chunk_size``, it is split into sub-chunks of at
        most ``chunk_size`` characters without breaking message lines.
        """
        import re
        msg_line = re.compile(r'^\[(\d{4}-\d{2}-\d{2})\]')

        lines = text.split("\n")
        if not lines:
            return []

        # Group lines by date
        day_groups: list[list[str]] = []
        current_day: list[str] = []
        current_date: str | None = None

        for line in lines:
            m = msg_line.match(line)
            if m:
                date_str = m.group(1)
                if date_str != current_date:
                    if current_day:
                        day_groups.append(current_day)
                    current_day = [line]
                    current_date = date_str
                else:
                    current_day.append(line)
            else:
                current_day.append(line)

        if current_day:
            day_groups.append(current_day)

        # Split oversized days into sub-chunks (keep message lines intact)
        chunks: list[str] = []
        for day_lines in day_groups:
            day_text = "\n".join(day_lines)
            if len(day_text) <= chunk_size:
                chunks.append(day_text)
            else:
                sub: list[str] = []
                sub_len = 0
                for line in day_lines:
                    line_len = len(line) + 1  # +1 for newline
                    if sub_len + line_len > chunk_size and sub:
                        chunks.append("\n".join(sub))
                        sub = [line]
                        sub_len = len(line)
                    else:
                        sub.append(line)
                        sub_len += line_len
                if sub:
                    chunks.append("\n".join(sub))

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
            data = json.loads(self._extract_json(stripped))
        except json.JSONDecodeError:
            pass

        if data is None:
            try:
                fix_reply = self._llm.chat(
                    "你的上一次输出无法被解析为JSON。请只输出合法JSON对象，不要markdown代码块，不要任何解释。",
                    [{"role": "user", "content": stripped}],
                )
                data = json.loads(self._extract_json(fix_reply.strip()))
            except Exception:
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

    def generate_opening(self, card_json: dict, user_role: str) -> str:
        """Generate context-aware opening based on character card + user role."""
        name = card_json.get("name", "角色")
        identity = card_json.get("identity", "")
        traits = "、".join(card_json.get("personality_traits", []))
        style = card_json.get("speaking_style", {})
        tone = style.get("tone", "")
        catchphrases = "、".join(style.get("catchphrases", []))

        prompt = (
            f"你是「{name}」，{identity}。\n"
            f"性格特点：{traits}\n"
            + (f"说话语气：{tone}\n" if tone else "")
            + (f"口癖：{catchphrases}\n" if catchphrases else "")
            + f"\n现在「{user_role}」来找你了。请以{name}的口吻说一句开场白，"
            "要体现你对这个人的态度和你们之间的关系。"
            "直接说台词，不要旁白、不要动作描写。30字以内。"
        )
        return self._llm.chat(
            f"你是「{name}」，请严格按照角色设定说话。只输出一句开场白，不要任何额外内容。",
            [{"role": "user", "content": prompt}],
        )

    # ── Auto-tagging ───────────────────────────────────────────────────

    def _auto_tag(self, card_dict: dict) -> list[str]:
        """Lightweight LLM call to pick 1-3 preset tags matching the card.

        Falls back to empty list on any error.
        """
        name = card_dict.get("name", "")
        identity = card_dict.get("identity", "")
        traits = "、".join(card_dict.get("personality_traits", []))
        background = (card_dict.get("background") or "")[:200]

        prompt = (
            f"根据以下角色卡信息，从预设标签中选择1-3个最匹配的标签，只返回JSON数组。\n"
            f"预设标签：{PRESET_TAGS}\n"
            f"角色名：{name}\n"
            f"身份：{identity}\n"
            f"性格：{traits}\n"
            f"背景：{background}"
        )
        try:
            reply = self._llm.chat(
                "你是一个角色分类助手。只返回JSON数组，不要任何其他内容。",
                [{"role": "user", "content": prompt}],
            )
            import re
            m = re.search(r"\[.*?\]", reply.strip(), re.DOTALL)
            if m:
                tags = json.loads(m.group(0))
                if isinstance(tags, list):
                    return [t for t in tags if t in PRESET_TAGS][:3]
            return []
        except Exception as exc:
            print(f"[distiller] Auto-tagging failed (silent): {exc}")
            return []

    # ── MapReduce internals ────────────────────────────────────────────

    async def _run_map_concurrent(
        self,
        chunks: list[str],
        character_name: str,
        on_chunk_done: "callable | None" = None,
        is_chat: bool = False,
    ) -> list[tuple[int, str]]:
        """Core Map — concurrent chunk analysis shared by sync and stream.

        Returns ordered (index, analysis_text) tuples.
        ``on_chunk_done(index, result)`` is called synchronously within the
        async loop each time a chunk finishes.
        """
        sem = asyncio.Semaphore(self.MAP_CONCURRENCY)
        done_count = [0]
        lock = asyncio.Lock()
        map_system_fn = self._map_system_prompt_chat if is_chat else self._map_system_prompt
        map_user_fn = self._map_user_prompt_chat if is_chat else self._map_user_prompt

        async def _one(i: int, chunk: str) -> tuple[int, str]:
            async with sem:
                system = map_system_fn(character_name)
                user = map_user_fn(chunk, character_name)
                try:
                    result, _ = await self._llm.async_chat(
                        system, [{"role": "user", "content": user}]
                    )
                except Exception as exc:
                    print(f"[distiller] Map chunk {i} failed: {exc}")
                    result = ""
            async with lock:
                done_count[0] += 1
            if on_chunk_done:
                on_chunk_done(i, result)
            return (i, result)

        tasks = [asyncio.create_task(_one(i, c)) for i, c in enumerate(chunks)]
        return await asyncio.gather(*tasks)

    async def _run_reduce_concurrent(
        self,
        batches: list[list[str]],
        character_name: str,
        on_batch_done: "callable | None" = None,
        sem_size: int = 6,
    ) -> list[tuple[int, str]]:
        """Run multiple Reduce batches concurrently with a semaphore."""
        sem = asyncio.Semaphore(sem_size)
        done_count = [0]
        lock = asyncio.Lock()

        async def _one(i: int, batch: list[str]) -> tuple[int, str]:
            async with sem:
                try:
                    result = await self._single_reduce_async(batch, character_name)
                except Exception as exc:
                    print(f"[distiller] Reduce batch {i} failed: {exc}")
                    result = ""
            async with lock:
                done_count[0] += 1
                current = done_count[0]
            if on_batch_done:
                on_batch_done(current, i, result)
            return (i, result)

        tasks = [asyncio.create_task(_one(i, b)) for i, b in enumerate(batches)]
        return await asyncio.gather(*tasks)

    def _single_reduce(self, raw_analyses: list[str], character_name: str) -> str:
        """Merge independent chunk analyses into a single profile (sync)."""
        combined = self._reduce_user_prompt(raw_analyses, character_name)
        result = self._llm.chat(
            self._reduce_system_prompt(character_name),
            [{"role": "user", "content": combined}],
        )
        usage = self._llm.last_usage
        self._try_record_usage("distill_reduce", usage)
        return result

    async def _single_reduce_async(self, raw_analyses: list[str], character_name: str) -> str:
        """Merge independent chunk analyses into a single profile (async, for concurrent batches)."""
        combined = self._reduce_user_prompt(raw_analyses, character_name)
        result, usage = await self._llm.async_chat(
            self._reduce_system_prompt(character_name),
            [{"role": "user", "content": combined}],
        )
        self._try_record_usage("distill_reduce", usage)
        return result

    def _single_reduce_stream(self, raw_analyses: list[str], character_name: str):
        """Merge independent chunk analyses into a single profile (streaming)."""
        combined = self._reduce_user_prompt(raw_analyses, character_name)
        yield from self._llm.chat_stream(
            self._reduce_system_prompt(character_name),
            [{"role": "user", "content": combined}],
        )
        usage = self._llm.last_usage
        self._try_record_usage("distill_reduce", usage)

    def _do_reduce(self, raw_analyses: list[str], character_name: str) -> str:
        """Auto-batching reduce: concurrent batches when > SAFE_SINGLE_REDUCE.

        Recurses on batch results until a single profile fits one prompt.
        """
        if len(raw_analyses) <= self.SAFE_SINGLE_REDUCE:
            return self._single_reduce(raw_analyses, character_name)
        batches = [
            raw_analyses[i : i + self.SAFE_SINGLE_REDUCE]
            for i in range(0, len(raw_analyses), self.SAFE_SINGLE_REDUCE)
        ]

        async def _concurrent() -> list[str]:
            results = await self._run_reduce_concurrent(batches, character_name)
            return [r[1] for r in sorted(results, key=lambda x: x[0]) if r[1].strip()]

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            merged = asyncio.run(_concurrent())
        else:
            merged = [self._single_reduce(b, character_name) for b in batches]
        return self._do_reduce(merged, character_name)

    # ── MapReduce public API ───────────────────────────────────────────

    def distill_incremental(
        self,
        text: str,
        character_name: str,
        aliases: list[str] | None = None,
        on_progress: "callable | None" = None,
        text_type: str = "story",
    ) -> CharacterCard:
        """MapReduce 蒸馏：Map 并发分析 → Reduce 合并 → Format 输出 CharacterCard。

        Args:
            text: 原始文本。
            character_name: 目标角色名。
            aliases: 角色别名列表，用于过滤相关片段。
            on_progress: ``(current, total)`` 进度回调。
            text_type: 'story' (默认) 或 'chat' (聊天记录预处理+专用提示词)。
        """
        is_chat = text_type == "chat"
        is_classic = text_type == "classic"

        # 参数分档：classic类型用更大的chunk和profile
        chunk_size = self._chunk_size
        max_profile_len = self._max_profile_len
        if is_classic:
            chunk_size = max(chunk_size, 6000)
            max_profile_len = max(max_profile_len, 12000)

        # Chat: Layer 0+1 already done at upload time; only Layer 2 here
        if is_chat:
            preprocessor = ChatPreprocessor()
            text = preprocessor._layer2_character_context(text, character_name)

        aliases = list(aliases) if aliases else []
        match_terms = [character_name] + aliases

        if is_chat:
            chunks = self._split_chunks_chat(text, chunk_size)
        else:
            chunks = self._split_chunks(text, chunk_size)

        relevant = [c for c in chunks if any(t in c for t in match_terms)]
        if not relevant:
            relevant = chunks[:3]

        total = len(relevant)
        completed = [0]
        lock = threading.Lock()

        def _on_done(_idx: int, _result: str) -> None:
            with lock:
                completed[0] += 1
            if on_progress:
                on_progress(completed[0], total)

        if on_progress:
            on_progress(0, total)

        # Run async Map in a dedicated thread (safe even under uvicorn async context)
        q: queue.Queue = queue.Queue()

        async def _map_wrapper():
            results = await self._run_map_concurrent(relevant, character_name, _on_done, is_chat)
            q.put(("done", results))

        def _thread_run():
            try:
                asyncio.run(_map_wrapper())
            except Exception as exc:
                q.put(("error", str(exc)))

        t = threading.Thread(target=_thread_run, daemon=True)
        t.start()

        map_results: list[tuple[int, str]] = []
        while True:
            kind, payload = q.get()
            if kind == "done":
                map_results = payload
                break
            if kind == "error":
                raise RuntimeError(f"Map 阶段失败：{payload}")
            # else: progress is handled via _on_done callback already

        t.join(timeout=5)

        if on_progress:
            on_progress(total, total)

        raw_analyses = [
            r[1] for r in map_results if r[1].strip() and r[1].strip() != "无"
        ]
        if not raw_analyses:
            raise ValueError("蒸馏失败：未能从任何片段中提取到角色信息")

        # Phase 2: Reduce — auto-batching merge
        profile_draft = self._do_reduce(raw_analyses, character_name)
        if not profile_draft.strip():
            raise ValueError("蒸馏失败：未能从文本中提取到角色信息")

        # Compress if needed
        if len(profile_draft) > max_profile_len:
            try:
                profile_draft = self._llm.chat(
                    f"压缩以下「{character_name}」的角色档案到{max_profile_len}字以内。\n"
                    "优先级：原文对话原句 > 行为证据 > 性格总结 > 背景信息。\n"
                    "口癖和说话风格的原文例句必须保留，这是最重要的。\n"
                    "合并重复信息，但不要删除矛盾点。",
                    [{"role": "user", "content": f"请压缩到{max_profile_len}字以内：\n\n{profile_draft}"}],
                )
            except Exception as exc:
                print(f"[distiller] Profile compression failed: {exc}")

        # Phase 3: Format — produce CharacterCard JSON
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

        self._try_record_usage("distill_format")

        stripped = reply.strip()
        data: Any | None = None
        try:
            data = json.loads(self._extract_json(stripped))
        except json.JSONDecodeError:
            pass

        if data is None:
            try:
                fix_reply = self._llm.chat(
                    "你的上一次输出无法被解析为JSON。请只输出合法JSON对象，不要markdown代码块，不要任何解释。",
                    [{"role": "user", "content": stripped}],
                )
                data = json.loads(self._extract_json(fix_reply.strip()))
            except Exception:
                raise ValueError("蒸馏失败：LLM 返回格式不正确") from None

        try:
            card = CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise ValueError("蒸馏失败：LLM 返回格式不正确") from exc

        # AI auto-tagging (fails open)
        try:
            card_dict = card.model_dump()
            tags = self._auto_tag(card_dict)
            if tags:
                card_dict["tags"] = tags
                # Re-validate so tags are included in model_dump()
                card = CharacterCard.model_validate(card_dict)
        except Exception as exc:
            print(f"[distiller] Auto-tagging failed (silent): {exc}")

        return card

    def distill_incremental_stream(
        self,
        text: str,
        character_name: str,
        aliases: list[str] | None = None,
        text_type: str = "story",
    ):
        """MapReduce 流式蒸馏 — 实时推送进度 + 流式 JSON 生成。

        yield 值类型：
        - dict: 进度事件（status: analyzing / merging / formatting / error）
        - str:  Format 阶段的 token 片段（SSE 路由累积为 card JSON）

        text_type: 'story' (默认) 或 'chat' (聊天记录预处理+专用提示词)
        """
        is_chat = text_type == "chat"
        is_classic = text_type == "classic"

        # 参数分档：classic类型用更大的chunk和profile
        chunk_size = self._chunk_size
        if is_classic:
            chunk_size = max(chunk_size, 6000)

        # Chat preprocessing
        # Chat: Layer 0+1 already done at upload time; only Layer 2 here
        if is_chat:
            preprocessor = ChatPreprocessor()
            text = preprocessor._layer2_character_context(text, character_name)

        aliases = list(aliases) if aliases else []
        match_terms = [character_name] + aliases

        if is_chat:
            chunks = self._split_chunks_chat(text, chunk_size)
        else:
            chunks = self._split_chunks(text, chunk_size)

        relevant = [c for c in chunks if any(t in c for t in match_terms)]
        if not relevant:
            relevant = chunks[:3]

        total = len(relevant)
        yield {"status": "analyzing", "current": 0, "total": total}

        # Select Map prompts per text type
        map_system = self._map_system_prompt_chat if is_chat else self._map_system_prompt
        map_user = self._map_user_prompt_chat if is_chat else self._map_user_prompt

        # ── Phase 1: Map — concurrent with per-chunk progress via thread+queue ──
        q: queue.Queue = queue.Queue()

        async def _map_with_progress() -> None:
            sem = asyncio.Semaphore(self.MAP_CONCURRENCY)
            done_count = [0]
            lock = asyncio.Lock()

            async def _one(i: int, chunk: str) -> tuple[int, str]:
                async with sem:
                    system = map_system(character_name)
                    user = map_user(chunk, character_name)
                    try:
                        result, _ = await self._llm.async_chat(
                            system, [{"role": "user", "content": user}]
                        )
                    except Exception as exc:
                        print(f"[distiller] Map chunk {i} failed: {exc}")
                        result = ""
                async with lock:
                    done_count[0] += 1
                    current = done_count[0]
                q.put(("chunk", current, i, result))
                return (i, result)

            tasks = [asyncio.create_task(_one(i, c)) for i, c in enumerate(relevant)]
            await asyncio.gather(*tasks)
            q.put(("done", None, None, None))

        def _thread_run() -> None:
            try:
                asyncio.run(_map_with_progress())
            except Exception as exc:
                q.put(("error", str(exc), None, None))

        t = threading.Thread(target=_thread_run, daemon=True)
        t.start()

        map_results: list[tuple[int, str]] = []
        while True:
            kind, a, b, c = q.get()
            if kind == "done":
                break
            if kind == "error":
                yield {"error": f"Map 阶段失败：{a}"}
                return
            if kind == "chunk":
                map_results.append((b, c))
                yield {"status": "analyzing", "current": a, "total": total}

        t.join(timeout=5)

        map_results.sort(key=lambda x: x[0])
        raw_analyses = [
            r[1] for r in map_results if r[1].strip() and r[1].strip() != "无"
        ]

        if not raw_analyses:
            yield {"error": "未能从任何片段中提取到角色信息"}
            return

        # ── Phase 2: Reduce — streaming with auto-batching ──
        if len(raw_analyses) <= self.SAFE_SINGLE_REDUCE:
            yield {"status": "merging", "current": 0, "total": 1}
            profile_draft = ""
            tc = 0
            for token in self._single_reduce_stream(raw_analyses, character_name):
                if token == "\x00THINKING\x00":
                    continue
                profile_draft += token
                tc += 1
                if tc % 50 == 0:
                    yield {"heartbeat": True}
            yield {"status": "merging", "current": 1, "total": 1}
        else:
            batches = [
                raw_analyses[i : i + self.SAFE_SINGLE_REDUCE]
                for i in range(0, len(raw_analyses), self.SAFE_SINGLE_REDUCE)
            ]

            rq: queue.Queue = queue.Queue()

            def _on_batch_done(done_count: int, idx: int, result: str) -> None:
                rq.put(("batch", done_count, idx, result))

            async def _reduce_batches() -> list[tuple[int, str]]:
                return await self._run_reduce_concurrent(
                    batches, character_name, _on_batch_done
                )

            def _reduce_thread() -> None:
                try:
                    asyncio.run(_reduce_batches())
                    rq.put(("done",))
                except Exception as exc:
                    rq.put(("error", str(exc)))

            rt = threading.Thread(target=_reduce_thread, daemon=True)
            rt.start()

            batch_by_index: dict[int, str] = {}
            while True:
                item = rq.get()
                kind = item[0]
                if kind == "done":
                    break
                if kind == "error":
                    yield {"error": f"Reduce 阶段失败：{item[1]}"}
                    return
                if kind == "batch":
                    _k, done_count, idx, result = item
                    batch_by_index[idx] = result
                    yield {"status": "merging", "current": done_count, "total": len(batches)}

            rt.join(timeout=5)

            batch_results: list[str] = []
            for i in range(len(batches)):
                result = batch_by_index.get(i, "")
                if result.strip():
                    batch_results.append(result)
                else:
                    print(f"[distiller] Reduce batch {i} returned empty, skipped")

            yield {"heartbeat": True}

            if len(batch_results) <= self.SAFE_SINGLE_REDUCE:
                profile_draft = ""
                tc = 0
                for token in self._single_reduce_stream(batch_results, character_name):
                    if token == "\x00THINKING\x00":
                        continue
                    profile_draft += token
                    tc += 1
                    if tc % 50 == 0:
                        yield {"heartbeat": True}
            else:
                profile_draft = self._do_reduce(batch_results, character_name)

        if not profile_draft.strip():
            yield {"error": "未能从文本中提取到角色信息"}
            return

        # ── Phase 3: Format — streaming JSON generation ──
        yield {"status": "formatting"}
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
                f"以下是关于「{character_name}」的完整分析档案，严格按 JSON 格式输出角色卡：\n\n{profile_draft}"
            }],
        )
        self._try_record_usage("distill_format")
