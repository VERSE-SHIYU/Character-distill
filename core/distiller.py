"""蒸馏引擎：从文本中识别角色并生成结构化角色卡。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
import collections.abc as _cabc

import yaml
from pydantic import ValidationError

from openai import AsyncOpenAI

from adapters.llm_adapter import LLMAdapter, incomplete_response_info, user_facing_error
from core.chat_preprocessor import ChatPreprocessor
from core.schema import CharacterCard, PRESET_TAGS
from core.utils import aggregate_usage, estimate_usage_from_chars, try_record_usage
from core import telemetry as T  # OTel 埋点
from core import concurrency as C  # 派生与上下文传播

if TYPE_CHECKING:
    from storage.base import StorageBase

# ── identify_characters TTL cache ───────────────────────────────────────
IDENTIFY_CACHE_TTL_SECONDS = 600
IDENTIFY_CACHE_MAX_ENTRIES = 100
_IDENTIFY_CACHE: OrderedDict[str, tuple[list[dict[str, Any]], float]] = OrderedDict()
_IDENTIFY_CACHE_LOCK = threading.Lock()


#: 别名收录规则：识别与合并**共用这一段文本**，不写两份。
#: 别名在下游是按子串匹配用的（蒸馏选片 `any(t in c for t in match_terms)`、RAG 打标签
#: `rag.py:_tag_characters`），所以一个多人共用的称呼一旦进了 aliases，就会把别人的场景
#: 误标给此人。判据交给 LLM，代码里不加子串/规则推断。
ALIAS_UNIQUENESS_RULE = (
    "aliases 只收录在全文范围内唯一指向此人的称呼；"
    "多人共用的泛称（如「二爷」同时指宝玉和贾琏、「太太」「老爷」「奶奶」）一律不收录。"
)

IDENTIFY_SYSTEM_PROMPT = (
    "阅读以下文本，找出所有有名字且有对话或行为描写的角色。\n"
    "关键要求：如果同一个人有多个称呼（全名、昵称、绰号、姓氏、官职、敬称、代称），"
    "必须归为一组。选最常用的全名作 name，其余放入 aliases。\n"
    + ALIAS_UNIQUENESS_RULE + "\n"
    '例如：魏无羡/魏婴/夷陵老祖 → name: "魏无羡", aliases: ["魏婴", "夷陵老祖"]\n'
    '例如：汪东城/大东 → name: "汪东城", aliases: ["大东"]\n'
    "\n"
    "只返回 JSON 数组，格式：\n"
    '[{"name": "主名", "aliases": ["别名1", "别名2"], '
    '"importance": "主要/次要", "reason": "简述"}]\n'
    "不要返回任何其他内容。"
)

#: 多分片识别的合并提示：各分片各列各的名单，同一人会在不同分片里被反复列出、
#: 且各分片看不到对方的判断——归组与主次必须在这一步按**全书**重判。输出格式与
#: ``IDENTIFY_SYSTEM_PROMPT`` 相同（都是 JSON 数组），故沿用同一个解析器。
IDENTIFY_MERGE_PROMPT = (
    "以下是从同一部作品的不同片段中各自识别出的角色名单，片段之间可能有重复。\n"
    "请合并为一份全书名单：\n"
    "1. 同一个人在不同片段里的不同称呼（全名、昵称、绰号、姓氏、官职、敬称、代称）"
    "必须归为一组，不要因为分片而重复列出；\n"
    "2. 选最常用的全名作 name，其余称呼放入 aliases；\n"
    "3. " + ALIAS_UNIQUENESS_RULE + "\n"
    "4. importance（主要/次要）按此人在**全书**中的戏份判，不按单个片段里的出现次数；\n"
    "5. reason 保留最具体的一条。\n"
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

## 前置步骤
先通读全文，枚举出所有【有名字且有对话或行为描写】的角色（包括前任、现任、配角等次要角色，戏份少也要算）；同一人有多个称呼的归为一组。后续维度 F 必须覆盖这里枚举出的全部角色。

## 分析维度（每个维度必须给出原文证据）

A. 基本信息：名字、身份、背景
B. 核心性格（3-5个）：每个特质 + 原文中的具体场景作为证据
C. 说话风格：语气、句式、口癖（直接从原文对话提取）、用词水平。禁忌用词（taboo_words）：根据角色性格，推断他绝对不会说出口的话或词（如违背人设的示弱话、不符其语言习惯的词）。从原文和人设推断，确实没有才留空。
D. 价值观（2-4个）：什么对他最重要？两难时怎么选？
E. 关键记忆（3-5个）：塑造此人的重要经历
F. 人际关系（站在本角色自己的视角，单向抽取）：
   覆盖前置步骤中枚举的【所有】有名字角色，逐一独立成条，不遗漏次要角色（前任/现任/配角），不合并不同角色，不列入宠物等非人物。
   关键：只写【本角色对对方的看法】，不要写"对方怎么看本角色"。关系可以不对称——A把B当挚友，B可能对A别有心思，各自视角各自抽，这是合理的。
   每条给出：
   - target：对方名（必须用前置步骤中的标准名，确保能和其他角色卡对上）
   - relation：关系类型（挚友/前队友/仇人/暗恋对象...）
   - attitude：态度描述（体现情感变化）
   - note：一句话口径——站在本角色的角度，"我和ta是什么关系、我怎么看ta"，这句会在对话中直接喂给模型当固定立场，要自然、口语、能直接用。
G. 内在矛盾（1-3个）：此人身上自相矛盾之处，以及矛盾如何影响行为
H. 开场白：以此角色的口吻写一句开场白，用于对话开始时
I. 对话示例（2-3轮）：从原文中提取最能体现此角色说话风格的2-3组对话交互。格式为"对方：xxx\n角色：xxx"。选择的对话必须能展示角色的口癖、语气、态度。如果原文有动作描写，用（）包裹保留，如"（冷笑）你以为你是谁？"
J. 情感模式（2-3个）：什么情况下会生气、开心、沉默、逃避？触发条件是什么？
K. 决策风格：面对选择时是冲动还是谨慎？靠情感还是逻辑？举例说明。
L. 角色弧线：此人从故事开始到结束经历了怎样的变化？分2-4个阶段描述，每阶段一句话。如果无明显变化则写"无明显变化"。
N. 认知/语言画像：基于原文中角色的实际话语和行为，判断以下四项：
   - education_level（文化程度）：文盲/识字不多/普通/受过良好教育/学者。从句式复杂度、用词丰富度、会不会用成语典故判断。原文证据：引一句原文中角色说的话作为判断依据。
   - knowledge_scope（知识边界）：这个角色的时代/阶层/见识决定他知道什么、不知道什么。如"古代农妇，不懂现代科技与时事", 或"现代都市白领，对古代文学不了解"。从角色身份、背景、行为推断。
   - speech_style（说话腔调）：用词雅俗、长短句风格、会不会用成语/专业词、口头禅、方言口音感。从原文对话提取最典型的说话特征。
   - vocabulary_level（用词层次）：粗白（市井/底层）/日常（普通人）/文雅（读书人/官员）/书面（学者/文人）。从原文用词直接判断。

M. 心理画像（用于情感动力学建模 — 依据 Kuppens 情感动力学 + PsyPlay 大五离散化）：
   - 大五人格：基于全文行为，给 openness/conscientiousness/extraversion/agreeableness/neuroticism 各 1-5 分（1 极低 5 极高）。必须符合真实人格分布，避免矛盾组合（如高神经质又高宜人性需有原文支撑才可同高）。
   - affinity_baseline（0-100）：此角色对一个新认识的人，默认会停在的关系基线。高冷/谨慎者低（25-40），热情/外向者高（55-70），多数人 45-55。
   - volatility：情绪波动幅度，平稳/适中/剧烈（高神经质偏剧烈）。
   - grudge_inertia：受到负面对待后多久消化，大度/一般/记仇（低宜人性或高神经质偏记仇）。
   - triggers（1-3 条）：碰了会让 ta 情绪激烈下降的具体雷点，从原文冲突场景提取。
   - soft_spots（1-3 条）：戳中会让 ta 心软/好感上升的点，从原文提取。

## 输出要求
严格按以下 JSON 格式输出，不要输出任何其他内容（不要 markdown 代码块标记）。把结论和关键依据（含出处）浓缩进一句话，出处用括号附句尾。严禁输出 {trait:..., description:...} 这种嵌套对象。

完整 JSON 模板（所有字段必须包含，psyche 为必需嵌套对象）：
{
  "name": "角色名",
  "identity": "一句话身份",
  "personality_traits": ["特质1（原文证据）", "特质2（原文证据）", "特质3（原文证据）"],
  "speaking_style": {
    "tone": "语气描述",
    "sentence_pattern": "句式特点描述",
    "catchphrases": ["口癖1", "口癖2"],
    "vocabulary_level": "文雅/日常/粗白",
    "taboo_words": ["禁忌词1", "禁忌词2"]
  },
  "values": ["核心价值观1", "核心价值观2"],
  "key_memories": ["关键经历1（原文出处）", "关键经历2（原文出处）"],
  "relationships": [
    {"target": "对方名", "relation": "关系类型", "attitude": "态度描述", "note": "一句话口径——本角色怎么看对方"}
  ],
  "inner_tensions": ["内在矛盾1（原文出处）", "内在矛盾2（原文出处）"],
  "background": "背景摘要",
  "first_message": "角色开场白",
  "dialogue_examples": ["对方：xxx\n角色：xxx"],
  "emotional_patterns": ["情感模式1（原文出处）", "情感模式2（原文出处）"],
  "decision_style": "决策风格描述（含原文依据）",
  "character_arc": ["阶段1变化", "阶段2变化"],
  "psyche": {
    "openness": 3,
    "conscientiousness": 3,
    "extraversion": 3,
    "agreeableness": 3,
    "neuroticism": 3,
    "affinity_baseline": 50,
    "volatility": "适中",
    "grudge_inertia": "一般",
    "triggers": ["雷点1（原文冲突场景）", "雷点2（原文冲突场景）"],
    "soft_spots": ["软肋1（原文出处）", "软肋2（原文出处）"]
  },
  "cognitive": {
    "education_level": "文盲/识字不多/普通/受过良好教育/学者",
    "knowledge_scope": "此角色的知识边界描述",
    "speech_style": "说话腔调描述（含原文例证）",
    "vocabulary_level": "粗白/日常/文雅/书面"
  }
}

重要：
- psyche 是必需嵌套对象，triggers 和 soft_spots 放在 psyche 内部，不在顶层
- personality_traits/values/key_memories/inner_tensions/emotional_patterns/character_arc/dialogue_examples 的每个元素是【一句字符串】，不是对象
- relationships 的每个元素是【对象】，含 target/relation/attitude/note 四个字段
- 数字字段（openness/conscientiousness 等）输出整数，不要加引号
- 所有字段必须按此模板输出，不要添加自定义字段
"""


class DistillError(ValueError):
    """蒸馏失败：**上屏口径与运维口径分离**。

    ``user_message`` 是上屏口径 —— 不含内部标识（finish_reason / 分片计数 / where /
    异常类名），也不含运维建议（抬 max_tokens、换 API key）。``str()`` 是运维口径，
    只进日志。

    路由层取上屏文案**必须经 ``adapters.llm_adapter.user_facing_error`` 这一唯一出口**：
    不要自己拼「蒸馏失败：」前缀（缺陷 17 的双重前缀就是源与路由各拼了一次），
    也不要 ``str(exc)`` / ``type(exc).__name__``。

    继承 ValueError：路径上既有的 ``except ValueError`` 语义不变。
    """

    def __init__(self, user_message: str, ops_detail: str = "") -> None:
        self.user_message = user_message
        super().__init__(f"{user_message}｜{ops_detail}" if ops_detail else user_message)


def _shape_ok(data: Any, required_keys: tuple[str, ...]) -> bool:
    """JSON 形状校验：dict 且含全部必需字段。

    曾嵌套在 _parse_json_with_retry 内（捕获 required_keys 的闭包）。提取为模块级
    纯函数，供续跑三重门的第二道复用同一份解析规则——复制会让两份漂移。
    """
    if not isinstance(data, dict):
        return False
    return all(k in data for k in required_keys)


def text_fingerprint(text: str) -> str:
    """sha256(utf-8) hex — 跨进程稳定的内容指纹。

    不要用内置 hash()：str 哈希受 PYTHONHASHSEED 影响，跨进程不稳定。任务级
    (text_fingerprint) 与分片级 (chunk_fingerprint) 共用此函数，避免两份漂移。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resume_hit(index: int, chunk: str, candidates: dict | None) -> str | None:
    """续跑分片三重门：三道全过才返回缓存 result，否则 None（需重跑）。

    1. candidates 里有该 index 且形状正确（result/fingerprint 两键齐全，_shape_ok）
    2. result 非空 —— 空结果不是可用结果
    3. chunk_fingerprint 与当前切分重算的 sha256 一致（该片原文未变）

    第 3 道用原文哈希而非 index：别名漂移会让 relevant 切片变化、index 语义漂移，
    哈希不一致即拒绝复用——宁重跑，不拼错位结果。

    第 2 道是**纵深防御**，不是契约：它挡的是「任何路径往 checkpoint 里写入空结果」
    这一类错误，不承诺结构校验——非空但内容不完整（截断的自由文本）照过。
    Map 返回的是自由文本角色证据，不产 JSON，所以「半截 JSON」不是本门的场景。
    主屏障在上游：distill_incremental_stream 里失败的 Map 片不落 checkpoint
    （抛异常即跳过 on_chunk_done），正常路径下这里根本不该出现空串候选。
    上游截断另由 adapters/llm_adapter.py 的 finish_reason 裁决层在源头变显式失败。
    """
    if not candidates:
        return None
    cand = candidates.get(index)
    if not _shape_ok(cand, ("result", "fingerprint")):
        return None
    if not (isinstance(cand["result"], str) and cand["result"].strip()):
        return None
    if cand["fingerprint"] != text_fingerprint(chunk):
        return None
    return cand["result"]


#: Map 阶段失败片的容忍上限：失败占比 **>** 此值即整批 bail，等于不算超。
_MAP_FAILURE_RATIO = 0.5


def _map_failure_exceeds_tolerance(failed: int, total: int) -> bool:
    """Map 失败率是否越过容忍上限 —— 同步路径与流式路径的**唯一**判据。

    调用方保证 ``total > 0``：空文本在 long-context 分流处就返回了，走不到 Map。

    原先两条路各写一遍 ``failed / total_chunks > 0.5``，改一处漏一处；识别若再加一份
    就是第三处。阈值与判据一并收在这里。
    """
    return failed / total > _MAP_FAILURE_RATIO


_T = TypeVar("_T")


def _run_async_in_ctx_thread(factory: _cabc.Callable[[], _cabc.Awaitable[_T]]) -> _T:
    """唯一的 sync→async 桥接：在派生线程里新建 loop 跑协程，把结果带回调用线程。

    调用方通常正处在 uvicorn 的事件循环里，不能原地 ``asyncio.run`` —— 故必须下到
    派生线程去建自己的 loop。**必须用 ``C.ctx_thread``**（缺陷 35）：记账身份靠
    contextvars 传播，裸 ``threading.Thread`` 会让派生线程读到空身份、账落空。

    收的是**工厂**而不是协程对象：协程绑定创建它的循环，跨线程传递是错的。

    **清理失败的语义（工厂方的义务，不由本函数承担）**：一次「主流程已成功」的调用，
    不能因为收尾（如关闭 async client）出错而整体失败。工厂自己把清理包住 —— 记日志
    （带异常本体）、不覆盖主结果、不上抛。**这不是新增的宽容，是把原有的意外行为变显式**：
    旧实现里工厂先把结果塞进 queue，消费方拿到即 break，随后 ``finally`` 里 close 抛的错
    成了没人读的第二个 queue item —— 同样不上抛，但**连日志都没有**。「静默吞掉」与
    「明确记一笔再继续」对调用方等价，对排障不等价。
    """
    outcome: queue.Queue = queue.Queue()

    def _thread_run() -> None:
        try:
            outcome.put((True, asyncio.run(factory())))
        except BaseException as exc:  # 含 KeyboardInterrupt：不能让它把 q.get() 悬死
            outcome.put((False, exc))

    t = C.ctx_thread(_thread_run, daemon=True)  # context 传播点
    t.start()
    ok, payload = outcome.get()
    t.join(timeout=5)
    if not ok:
        raise payload
    return payload


class Distiller:
    """基于 LLM 的角色识别与角色卡蒸馏。"""

    SAFE_SINGLE_REDUCE = 80
    CARD_MAX_TOKENS = 8192  # 角色卡 JSON 长输出需要更大 token 上限
    #: 合并全书名单的输出上限 —— 整本书的花名册装不进 CARD_MAX_TOKENS。
    #: 依据：DeepSeek 官方 deepseek-v4-pro 最大输出 384K
    #: （https://api-docs.deepseek.com/quick_start/pricing）；红楼梦级名单估算 2–3 万字符
    #: ≈ 1.5–2 万 tokens，取约 3 倍余量。流式没有总时长上限（核实见
    #: adapters/llm_adapter.py:720-738：budget 只包 create() 返回前），故这个值只受模型
    #: 输出上限约束，不被生成轮 45s/60s 的墙钟夹逼。**只给合并用**：不改 config.yaml，
    #: 也不动其他路径的 CARD_MAX_TOKENS。真撞上限仍按截断路径抛，不加兜底。
    IDENTIFY_MERGE_MAX_TOKENS = 65536
    #: 角色识别算法的版本：口径（提示词 / 覆盖范围 / 合并规则）一改就 +1。
    #: 名单落库时带此版本，读回时版本不符即当无缓存 —— 旧版本的名单是残缺的
    #: （只覆盖前 1 万字那版只认头两章），沿用比重算更糟。值是**唯一定义**，
    #: 存储层与 `core/character_roster.py` 都从这里取，不许各写各的字面量。
    IDENTIFY_VERSION = 2

    def __init__(
        self,
        llm: LLMAdapter,
        config_path: str | Path | None = None,
        storage: StorageBase | None = None,
    ) -> None:
        """初始化蒸馏器。

        Args:
            llm: 已配置好的大模型适配器。
            config_path: 配置文件路径；默认读取仓库根目录 ``config.yaml``。
            storage: 记账落库用的存储（依赖，非身份）。生产由唯一装配出口
                `web/deps.get_distiller` 注入；直接 ``Distiller(llm)`` 的地方记不上账。

        **身份不走构造参数**：user_id 逐请求变化，放在 `core.request_context`
        的上下文里（缺陷 35）—— 构造期注入等于每个构造点都要记得写一遍，
        而漏掉的那次只会留一行日志。
        """
        self._llm = llm
        self._storage = storage
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
        self._longctx_threshold: int = int(distill_cfg.get("longctx_threshold", 150000))
        self._map_concurrency: int = max(1, int(distill_cfg.get("map_concurrency", 30)))

    def _try_record_usage(self, action: str = "distill", usage: dict | None = None) -> None:
        try_record_usage(
            storage=self._storage,
            llm=self._llm,
            action=action,
            usage=usage,
            source="Distiller",
        )

    def effective_chunk_size(self, text_type: str = "story") -> int:
        """本次 text_type 下实际使用的分片字符数（任务级 checkpoint 的一部分）。

        classic 强制放大到 ≥6000，与 map 路径分档一致。续跑的任务级门用它比对：
        切分参数变了则整批重跑（index 语义已变），不进分片级三重门。
        """
        return max(self._chunk_size, 6000) if text_type == "classic" else self._chunk_size

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
            "4. 不要为控制篇幅而删减信息，尽可能完整保留人物的性格、关系、记忆细节；原文对话和口癖优先保留"
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
        """剥掉 markdown 围栏、前后解释文字、修尾随逗号，提取完整 JSON 对象。

        处理 LLM 常见脏输出：````json` 围栏、JSON 前后的自然语言解释、
        尾随逗号（trailing comma）、以及嵌套大括号场景。
        """
        t = text.strip()

        # 1. 剥 markdown code fence —— 支持 ```json ... ``` 和 ``` ... ```
        if t.startswith("```"):
            # 找到第二个 ``` 作为 fence 结束
            parts = t.split("```")
            # parts[0] = "" (opening fence), parts[1] = maybe "json\n...", parts[2..] = rest
            if len(parts) >= 3:
                # 取第一个 fence 和第二个 fence 之间的内容
                t = parts[1]
                if t.startswith("json"):
                    t = t[4:]
                t = t.strip()
            elif len(parts) == 2:
                # 只有开头 fence 没有结尾 (````... 开头但没有闭合)
                t = parts[1].strip()

        # 2. 提取 JSON —— 同时支持对象 {} 和数组 []
        #    取第一个有效的 { 或 [ 作为起点，对应闭合符的最后一个作为终点
        brace_pos = t.find("{")
        bracket_pos = t.find("[")
        first_brace = brace_pos if brace_pos != -1 else float("inf")
        first_bracket = bracket_pos if bracket_pos != -1 else float("inf")

        if first_brace == float("inf") and first_bracket == float("inf"):
            return t  # 没有任何 JSON 结构

        is_array = first_bracket < first_brace
        if is_array:
            open_ch, close_ch = "[", "]"
            start = bracket_pos
            end = t.rfind("]")
        else:
            open_ch, close_ch = "{", "}"
            start = brace_pos
            end = t.rfind("}")

        if end <= start:
            return t

        candidate = t[start:end + 1]

        # 括号配对校验（跳过字符串内容）
        depth = 0
        in_string = False
        escaped = False
        for ch in candidate:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
        if depth == 0:
            t = candidate
        # 括号不成对时保留 candidate（尽力而为）

        # 3. 去尾随逗号：},] 和 ,] ， ]
        t = re.sub(r",\s*([}\]])", r"\1", t)

        return t

    # ── JSON parse with retry ─────────────────────────────────────────

    @staticmethod
    def _looks_truncated(text: str) -> bool:
        """检测 JSON 文本是否被截断（结尾不完整）。"""
        t = text.strip()
        if not t:
            return False
        if t[-1] not in ("}", "]", '"'):
            return True
        # 括号不成对 = 结构未闭合
        return t.count("{") != t.count("}") or t.count("[") != t.count("]")

    @staticmethod
    def _prompt_chars(system_prompt: str, messages: list[dict[str, Any]]) -> int:
        """一次调用实际喂进去的字符总数 —— 估算账的输入（两侧字符 → token 估算）。

        流式与非流式两条截断路共用这一个口径：同一段 prompt 在两支里算出同一个数，
        免得「按字符估算」这个数各写一遍、迟早漂成两个。
        """
        return len(system_prompt) + sum(len(str(m.get("content", ""))) for m in messages)

    def _collect_stream(
        self, system_prompt: str, messages: list[dict[str, Any]], label: str,
        usage_action: str, max_tokens: int | None = None,
    ) -> tuple[str, bool]:
        """流式收全文 → ``(文本, 上游是否已确定截断)``，**并在本级记账**。

        长输出（红楼梦级的名单合并）非流式必然撞墙：生成轮有 45s 单次 / 60s 总墙钟上限，
        流式一旦开始吐字就不再有总时长夹逼（`chat_stream` 的 deadline 只包 ``create()``
        返回前，见 adapters/llm_adapter.py）。所以长输出这条路两处都得流式。

        截断时流里已有正文仍然交出去：``chat_stream`` 在吐最后一片**之前**校验
        finish_reason（校验不过那片不交付），异常里的 content 是空的，但累积到此刻的
        部分正文还在本函数手里 —— 半截正文是重修的证据，不是要丢掉的东西。这也是本
        函数与 `_chat_accounted` 非流式那支的唯一差别：那支的正文挂在异常上（``info[1]``）。

        **记账落在发起调用的这一级**（形态锁的判据）：谁消费响应谁把账记进唯一出口
        （`_try_record_usage`），调用方不必记得补一笔 —— 靠调用方代记是约定不是机制，
        新增一个调用方漏写就是一段没账的成本。成功时流已耗尽，`last_usage` 立刻读
        （晚了会被下一次调用覆盖）。截断与其余失败都按字符估算补记：usage chunk 排在
        finish_reason **之后**，校验不过就不交付，故截断时 `last_usage` 必为 ``None``；
        而失败调用同样烧了 token（重试墙下空烧）—— 只记成功会让统计系统性偏低。
        """
        prompt_chars = self._prompt_chars(system_prompt, messages)
        parts: list[str] = []
        try:
            for piece in self._llm.chat_stream(
                system_prompt, messages,
                max_tokens=self.CARD_MAX_TOKENS if max_tokens is None else max_tokens,
            ):
                parts.append(piece)
        except Exception as exc:
            text = "".join(parts)
            info = incomplete_response_info(exc)
            self._try_record_usage(
                usage_action, estimate_usage_from_chars(prompt_chars, len(text)),
            )
            if info is None or info[0] != "length":
                print(f"调用 LLM 进行{label}失败：{exc}")
                raise
            return text, True
        self._try_record_usage(usage_action)
        return "".join(parts), False

    def _chat_accounted(
        self, system_prompt: str, messages: list[dict[str, Any]], label: str, action: str,
        stream: bool = False, max_tokens: int | None = None,
    ) -> tuple[str, bool]:
        """调一次 LLM 并把这一笔记进唯一出口 → ``(回复文本, 上游是否已确定截断)``。

        **初次与重修共用**（`_parse_json_with_retry` 的重修环也走这里）：两处只是提示词
        不同，记账口径不该因轮次再分家。

        ``length`` 截断是**上游确定信号**：不抛——把已生成的部分正文当截断证据交给
        `_parse_json_with_retry` 走重修环，比 `_looks_truncated` 从文本形状猜可靠。
        其余失败（网络、content_filter、资源不足）与截断无关、重修无用，原样上抛。

        ``stream=True`` 走 `_collect_stream`（长输出用），截断口径完全相同，记账落在
        那个原语里（同样是发起调用的那一级）。

        记账不变量（缺陷 16）：三个站点（`distill` / `_distill_longcontext` /
        `distill_incremental` 的收尾格式化）原本各记各的——两处各补一条
        `_try_record_usage`、`distill` 那处根本没有，口径不一致且初次调用漏记。
        收敛到这里：**一次调用恰记一条 usage，与它结果如何无关** —— token 花出去就
        花出去了，成功、截断、硬失败三种收尾都是同一次调用。

        截断与硬失败那两路**拿不到 `last_usage`**：`chat()` 进本轮就先清空，而
        `_extract_content` 的抛出点在 usage 回写之前，故只能按字符估算补记 ——
        截断支带上已生成的半截正文（缺陷 91），硬失败支 completion 侧为 0（缺陷 92），
        与流式支 `_collect_stream` 的 except 支同口径。
        """
        _mt = self.CARD_MAX_TOKENS if max_tokens is None else max_tokens
        if stream:
            return self._collect_stream(system_prompt, messages, label, action, _mt)
        truncated = False
        usage = None
        try:
            reply = self._llm.chat(system_prompt, messages, max_tokens=_mt)
        except Exception as exc:
            info = incomplete_response_info(exc)
            if info is None or info[0] != "length" or not info[1]:
                # 硬失败照样烧了 token（重试墙下正是空烧）—— 与截断支、流式支同口径：
                # 先记一条估算账再原样上抛。只记成功会让统计系统性偏低（缺陷 91 同形）。
                self._try_record_usage(action, estimate_usage_from_chars(
                    self._prompt_chars(system_prompt, messages)))
                print(f"调用 LLM 进行{label}失败：{exc}")
                raise
            reply, truncated = info[1], True
            usage = estimate_usage_from_chars(
                self._prompt_chars(system_prompt, messages), len(reply))
        self._try_record_usage(action, usage)
        return reply, truncated

    def _parse_json_with_retry(
        self, reply: str, retry_prompt: str, retry_messages: list[dict[str, Any]],
        action_label: str = "distill", required_keys: tuple[str, ...] = ("name",),
        upstream_truncated: bool = False,
        list_item_keys: tuple[str, ...] | None = None,
        stream: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """增强 JSON 解析：清理 → fix_reply → 重调 LLM，最多 3 次尝试。

        Args:
            reply: LLM 原始返回文本。
            retry_prompt: 重试时的 system prompt（用于 fix_reply 和完整重试）。
            retry_messages: 重试时的 user messages（用于完整重新调用 LLM）。
            action_label: 用量记录标签。
            required_keys: 结构校验——dict 必须包含的字段。仅靠
                ``isinstance(data, dict)`` 无法区分"合法 JSON 但结构错误"
                （例如 LLM 跑题吐出 ``{"status": "..."}``）和真正的角色卡，
                这里在 json.loads 之后补一道形状检查，三次尝试都生效。
            upstream_truncated: 上游是否已确定截断（`_chat_accounted` 拿到 length
                终态时为 True）。确定信号预置 ``truncated``，无需再从文本形状猜；
                厂商不回 finish_reason 时保持 False，由 `_looks_truncated` 兜底。
            list_item_keys: 目标形态切到**数组**——非空、每项都是含这些字段的 dict。
                不给时是角色卡（dict + ``required_keys``）。识别结果的合并走这一支：
                它与 ``IDENTIFY_SYSTEM_PROMPT`` 输出同格式（JSON 数组），所以共用这
                同一个重修环，而不是再写一份。
            stream: 两次重修调用是否走流式。初次调用已经是流式的长输出（合并），
                重修若退回非流式就还是会撞 45s/60s 的生成墙钟上限——通道必须一致。
            max_tokens: 重修调用的输出上限，默认 ``CARD_MAX_TOKENS``。长输出（合并）
                要显式抬高：上限不够时重修出来还是半截，重试没有意义。

        Returns:
            解析后的 dict（默认）或 list[dict]（``list_item_keys`` 给定时）。

        Raises:
            ValueError: 所有尝试均失败时抛出，附带可读消息和原始输出摘要。
        """
        import time as _time

        # 形状判据与措辞按目标形态分档。dict 那支的 prompt 文案逐字节不变（只多一层闭包）。
        if list_item_keys is None:
            def _accept(data: Any) -> bool:
                return _shape_ok(data, required_keys)

            def _is_shape_candidate(data: Any) -> bool:
                return isinstance(data, dict)

            def _detail(data: Any, prefix: str) -> str:
                if isinstance(data, dict):
                    return (f"{prefix}dict missing required keys {required_keys}: "
                            f"got keys {list(data.keys())}")
                return f"{prefix}value is not a dict"

            _shape_noun = "角色卡结构"
            _expected_kind = "的完整 JSON 对象"
            schema_hint = "，必须包含字段：" + "、".join(required_keys) if required_keys else ""
        else:
            def _accept(data: Any) -> bool:
                # 空数组不算合格：合并把整本书的角色丢光了，是失败不是答案
                return (
                    isinstance(data, list) and len(data) > 0
                    and all(_shape_ok(item, list_item_keys) for item in data)
                )

            def _is_shape_candidate(data: Any) -> bool:
                return isinstance(data, list)

            def _detail(data: Any, prefix: str) -> str:
                if isinstance(data, list):
                    return (f"{prefix}list items missing required keys {list_item_keys} "
                            f"(或数组为空)：{len(data)} 项")
                return f"{prefix}value is not a list"

            _shape_noun = "角色数组结构"
            _expected_kind = "的完整 JSON 数组"
            schema_hint = "，每项必须包含字段：" + "、".join(list_item_keys)

        def _repair(system: str, messages: list[dict[str, Any]]) -> tuple[str, bool]:
            """重修调用 → ``(文本, 上游是否已确定截断)``。通道与记账都与初次调用同路。

            直接委托 `_chat_accounted`：重修不再自带一份记账（写在本函数里就等于
            「调用方要记得补账」，流式那支还会与 `_collect_stream` 的本级记账**双计**）。
            """
            return self._chat_accounted(
                system, messages, action_label, action_label,
                stream=stream, max_tokens=max_tokens,
            )

        attempts = 0
        last_error = None
        last_bad_shape_reply = None  # 记下"语法合法但结构不对"的那一次，供 Attempt 2 使用
        # 截断证据：上游确定信号预置，没信号时由 Attempt 1 的 _looks_truncated 猜
        truncated: bool = upstream_truncated

        # Attempt 1: direct parse with strengthened extraction
        attempts += 1
        try:
            _extracted = self._extract_json(reply.strip())
            data = json.loads(_extracted)
            if _accept(data):
                T.set_current_attr("repair_stage", 1)
                return data
            last_error = _detail(data, "parsed ")
            if _is_shape_candidate(data):
                last_bad_shape_reply = reply.strip()
        except json.JSONDecodeError as exc:
            if Distiller._looks_truncated(_extracted):
                truncated = True
                last_error = f"JSON 被截断（max_tokens 可能不足）：{exc}"
            else:
                last_error = str(exc)

        # Attempt 2: ask LLM to fix its own output.
        # 区分两种失败：JSON 语法错误 vs JSON 合法但结构不对（如返回了一句对话回复）。
        # 后一种情况里"无法被解析"是假话，必须如实告诉 LLM 缺了什么字段，并把 schema 带上，
        # 否则 LLM 不知道该往哪个方向修。
        attempts += 1
        try:
            if last_bad_shape_reply is not None:
                fix_system = (
                    f"你的上一次输出是合法的 JSON，但不是要求的{_shape_noun}——"
                    f"缺少必需字段{schema_hint}。\n"
                    "你可能误把这当成了一次对话来回复。请重新输出，"
                    f"只返回符合下方 Schema{_expected_kind}，不要markdown代码块，不要任何解释，"
                    "不要输出对话或状态消息。\n\n"
                    f"Schema:\n{retry_prompt}"
                )
                fix_user_content = last_bad_shape_reply
            else:
                if truncated:
                    fix_system = (
                        "你的上一次输出因长度超限被截断，JSON 不完整。"
                        "请精简输出——合并重复描述、缩短长篇背景、保留核心人设和原文台词即可。"
                        "只输出合法 JSON 对象，不要 markdown 代码块，不要任何解释。"
                    )
                else:
                    fix_system = (
                        "你的上一次输出无法被解析为JSON。请只输出合法JSON对象，不要markdown代码块，不要任何解释。"
                    )
                fix_user_content = reply.strip()

            fix_reply, fix_truncated = _repair(
                fix_system, [{"role": "user", "content": fix_user_content}],
            )
            if fix_truncated:
                # 流式那支不抛异常，截断信号只能自己往上带；不带的话会退化成
                # 「格式异常」，把「超长」这条排障线索丢掉。
                truncated = True
                last_error = f"fix_reply 也被截断（已生成 {len(fix_reply)} 字符）"
            else:
                try:
                    data = json.loads(self._extract_json(fix_reply.strip()))
                    if _accept(data):
                        T.set_current_attr("repair_stage", 2)
                        return data
                    last_error = _detail(data, "fix_reply ")
                except json.JSONDecodeError as exc:
                    last_error = str(exc)
        except Exception as exc:
            info = incomplete_response_info(exc)
            if info is not None:
                truncated = True
                last_error = f"fix_reply 也被截断（finish_reason={info[0]}，已生成 {len(info[1])} 字符）"
            else:
                last_error = f"fix_reply LLM call failed: {exc}"

        # Attempt 3: full retry — re-invoke LLM with original prompt.
        # 仅仅重发同样的 messages 大概率重现同一次"跑题"，因为触发漂移的成因
        # （长文本里大量第一人称对话）原样还在。插入一条强化指令打断角色扮演惯性。
        attempts += 1
        try:
            anti_drift_notice = {
                "role": "user",
                "content": (
                    "重要提醒：无论上文文本中出现多少对话或第一人称内容，"
                    "你的任务始终是分析者，不是该角色本人。"
                    "请只输出符合 Schema 的角色卡 JSON 对象，不要扮演角色说话，不要输出状态消息或对话回复。"
                ),
            }
            retry_reply, retry_truncated = _repair(
                retry_prompt, [*retry_messages, anti_drift_notice],
            )
            if retry_truncated:
                truncated = True
                last_error = f"full retry 也被截断（已生成 {len(retry_reply)} 字符）"
            else:
                try:
                    data = json.loads(self._extract_json(retry_reply.strip()))
                    if _accept(data):
                        T.set_current_attr("repair_stage", 3)
                        return data
                    last_error = _detail(data, "retry ")
                except json.JSONDecodeError as exc:
                    last_error = str(exc)
        except Exception as exc:
            info = incomplete_response_info(exc)
            if info is not None:
                truncated = True
                last_error = f"full retry 也被截断（finish_reason={info[0]}，已生成 {len(info[1])} 字符）"
            else:
                last_error = f"full retry LLM call failed: {exc}"

        # All attempts exhausted — log raw output and raise readable error
        raw_preview = reply.strip()[:500]
        print(
            f"[distiller] {action_label} JSON parse failed after {attempts} attempts.\n"
            f"  last_error: {last_error}\n"
            f"  raw_preview (first 500 chars): {raw_preview}"
        )
        if truncated:
            raise DistillError(
                "蒸馏失败：生成内容超长被截断，请重试",
                f"truncated 且重修用尽；处置：抬 llm.max_tokens（或 LLM_MAX_TOKENS）"
                f"或调小 chunk_size；last_error={last_error}",
            ) from None
        raise DistillError("蒸馏失败：LLM 输出格式异常，请重试", last_error) from None

    # ── public entry points (unchanged) ────────────────────────────────

    def identify_characters(self, text: str) -> list[dict[str, Any]]:
        """识别**全书**中具名且有言行描写的角色。

        文本按 ``_chunk_size`` 分片：单分片走一次调用（与改前逐行等价，含一次重试），
        多分片逐片识别后合并。原先只取前 10000 字 —— 红楼梦这类长篇只覆盖头两章，
        名单天然残缺，而残缺名单会被落库、被所有下游当成全书名单用。

        ``IDENTIFY_VERSION`` 是识别口径的版本号，但**这里不管版本**：进程内 TTL 缓存
        是本进程自己刚算出来的，落库的版本判定在 `core/character_roster.py` 那一层。

        Args:
            text: 原始叙事文本（全文）。

        Returns:
            角色信息字典列表，**名单可以为空**（全书真的没有具名角色）。空名单照样
            落缓存：它是一次成功的识别结果。

        Raises:
            DistillError: 识别失败 —— 单分片两次解析均失败，或多分片失败率越过容忍线。
                失败走异常，因此**天然不会进缓存**，无需调用方额外判断。
        """
        key = text_fingerprint(text) + ":" + self._llm.model

        # Check cache
        with _IDENTIFY_CACHE_LOCK:
            cached = _IDENTIFY_CACHE.get(key)
            if cached is not None:
                result, ts = cached
                if time.time() - ts < IDENTIFY_CACHE_TTL_SECONDS:
                    print("[distill] identify cache hit")
                    # Deep copy to prevent caller mutation from polluting cache
                    return json.loads(json.dumps(result))
                else:
                    del _IDENTIFY_CACHE[key]

        chunks = self._split_chunks(text, self._chunk_size)
        if len(chunks) <= 1:
            result = self._identify_single_call(text)
        else:
            result = self._identify_over_chunks(chunks)

        # 只缓存成功结果（空名单也算成功）。失败在上面就抛了，走不到这里 —— 缓存
        # 住一个「没有角色」的错误答案，十分钟内所有调用都跟着错。
        # deep copy 隔离调用方的改动。
        with _IDENTIFY_CACHE_LOCK:
            _IDENTIFY_CACHE[key] = (json.loads(json.dumps(result)), time.time())
            if len(_IDENTIFY_CACHE) > IDENTIFY_CACHE_MAX_ENTRIES:
                _IDENTIFY_CACHE.popitem(last=False)
        return result

    @staticmethod
    def _normalize_identify_items(parsed: Any) -> list[dict[str, Any]]:
        """识别/合并结果的统一归一：补 aliases、丢掉非对象项（带告警）。"""
        if not isinstance(parsed, list):
            print("角色识别结果不是 JSON 数组")
            raise TypeError("expected JSON array")
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(parsed):
            if isinstance(item, dict):
                item.setdefault("aliases", [])
                out.append(item)
            else:
                print(f"警告：角色识别数组第 {idx} 项不是对象，已跳过")
        return out

    @classmethod
    def _parse_identify_list(cls, raw: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(cls._extract_json(raw))
        except json.JSONDecodeError as exc:
            print(f"解析角色识别 JSON 失败：{exc}")
            raise
        return cls._normalize_identify_items(parsed)

    def _identify_single_call(self, text: str) -> list[dict[str, Any]]:
        """单分片的识别：一次调用 + 一次重试（与原实现逐行等价）。

        Returns:
            角色列表，**可以为空**（这一段真的没有具名角色）。

        Raises:
            DistillError: 两次解析均失败。解析不出来是**失败**，不是「没有角色」——
                返回空列表会让上游把它当成一份合法的空名单。
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        try:
            reply = self._llm.chat(IDENTIFY_SYSTEM_PROMPT, messages)
        except Exception as exc:
            print(f"调用 LLM 进行角色识别失败：{exc}")
            raise
        # 紧跟在调用后读 last_usage —— 下一次调用会覆盖它，攒着记必然串号
        self._try_record_usage("distill_identify")

        try:
            return self._parse_identify_list(reply)
        except Exception:
            retry_prompt = IDENTIFY_SYSTEM_PROMPT + "请只返回JSON数组"
            try:
                reply_retry = self._llm.chat(retry_prompt, messages)
            except Exception as exc:
                print(f"角色识别重试调用 LLM 失败：{exc}")
                raise
            self._try_record_usage("distill_identify")
            try:
                return self._parse_identify_list(reply_retry)
            except Exception as exc:
                raise DistillError(
                    "识别失败：模型返回的角色名单无法解析，请重试",
                    f"单分片两次解析均失败；最后错误：{exc}",
                )

    def _identify_over_chunks(self, chunks: list[str]) -> list[dict[str, Any]]:
        """逐片识别 + 合并。

        失败率（调用失败 + 结果解析失败）越过 ``_map_failure_exceeds_tolerance`` 即抛：
        拿半本书的名单当全书名单，比报错更糟。未越线则继续 —— 合并那一步只看拿到的名单。

        未越线、且每一片都解析成空名单 → 返回 ``[]``：这是**真的没有具名角色**，
        不是识别失败。与单分片同口径 —— 空名单是合法结果，失败才抛。
        """
        def _build_prompt(chunk: str) -> tuple[str, str]:
            return IDENTIFY_SYSTEM_PROMPT, chunk

        map_results, failures = self._run_map_with_client(
            chunks, _build_prompt, "distill_identify",
        )

        total = len(chunks)
        parse_failed = 0
        parts: list[str] = []
        for _idx, raw in map_results:
            if not raw.strip():
                continue          # 该片已在 failures 里计过
            try:
                items = self._parse_identify_list(raw)
            except Exception as exc:
                print(f"[distiller] identify chunk parse failed: {exc}")
                parse_failed += 1
                continue
            if items:
                # 重新序列化：交给合并的是**已归一**的名单，格式统一、可直接被解析
                parts.append(json.dumps(items, ensure_ascii=False))

        failed = len(failures) + parse_failed
        if _map_failure_exceeds_tolerance(failed, total):
            last_error = str(failures[-1][1]) if failures else "分片结果无法解析为角色数组"
            if "429" in last_error:
                raise DistillError(
                    "识别失败：上游接口限流，请稍后重试",
                    f"API 429；{failed}/{total} 个分片识别失败",
                )
            raise DistillError(
                "识别失败：部分片段处理失败，请重试",
                f"{failed}/{total} 个分片识别失败；最后错误：{last_error}",
            )
        if failed:
            print(f"[distiller] {failed}/{total} identify chunks failed (within tolerance), continuing")

        if not parts:
            # 每一片都解析成空名单（且失败率未越线）→ 真空名单，不是失败。
            # 原先这里抛 DistillError，把「这本书没有具名角色」当成了故障。
            return []
        return self._identify_merge(parts)

    def _identify_merge(self, parts: list[str]) -> list[dict[str, Any]]:
        """把各分片的名单合并成一份全书名单：归组、按全书重判主次。"""
        body = (
            "以下是从同一部作品的不同片段中各自识别出的角色名单：\n\n"
            + "\n\n---片段分隔---\n\n".join(
                f"[分片 {i + 1}]\n{p}" for i, p in enumerate(parts)
            )
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": body}]
        # 走 _chat_accounted：它把账记进唯一出口（缺陷 16），且能把上游确定的 length
        # 截断信号带进重修环，而不是让半截 JSON 直接抛。
        # stream=True：红楼梦级的名单合并不是非流式那 45s/60s 墙钟能装下的输出。
        # max_tokens 显式抬到 IDENTIFY_MERGE_MAX_TOKENS：CARD_MAX_TOKENS 装不下全书名单，
        # 初次调用与重修都用它（重修上限不够时重修出来还是半截）。
        reply, upstream_truncated = self._chat_accounted(
            IDENTIFY_MERGE_PROMPT, messages, "角色识别合并", "distill_identify",
            stream=True, max_tokens=self.IDENTIFY_MERGE_MAX_TOKENS,
        )
        merged = self._parse_json_with_retry(
            reply, IDENTIFY_MERGE_PROMPT, messages,
            action_label="distill_identify", list_item_keys=("name",),
            upstream_truncated=upstream_truncated, stream=True,
            max_tokens=self.IDENTIFY_MERGE_MAX_TOKENS,
        )
        return self._normalize_identify_items(merged)

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
        self, text: str, characters: list[dict[str, Any]], chunk_size: int = 6000,
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
            start = end

        usages: list[dict | None] = []

        async def _resolve_chunk(chunk_text: str) -> str:
            result, usage = await self._llm.async_chat(
                system_prompt,
                [{"role": "user", "content": chunk_text}],
            )
            usages.append(usage)
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

        try:
            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    # context 传播点：submit 不拷贝 contextvar → ctx_submit
                    results = C.ctx_submit(pool, lambda: asyncio.run(_resolve_all())).result()
            else:
                results = asyncio.run(_resolve_all())
        finally:
            # 分片级并发调用整个烧掉的那段 token 在这里补记：整阶段汇总一条 + 调用次数。
            # 放 finally —— gather 半途炸掉时已完成的分片也是花掉的钱，不记即系统性偏低。
            merged = aggregate_usage(usages, len(usages))
            if merged is not None:
                self._try_record_usage("distill_coref", merged)

        return "".join(results)

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

        reply, upstream_truncated = self._chat_accounted(system_prompt, user_messages, "角色蒸馏", "distill")

        data = self._parse_json_with_retry(
            reply, system_prompt, user_messages,
            action_label="distill", upstream_truncated=upstream_truncated,
        )
        try:
            return CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc

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

        yield from self._llm.chat_stream(system_prompt, user_messages, max_tokens=self.CARD_MAX_TOKENS)
        # 流耗尽后 last_usage 才可读（_distill_longcontext_stream 同形）——生成器被消费方
        # 半途丢弃时这行不会执行，与既有的流式记账口径一致，不额外兜底。
        self._try_record_usage("distill_stream")

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
        reply = self._llm.chat(
            f"你是「{name}」，请严格按照角色设定说话。只输出一句开场白，不要任何额外内容。",
            [{"role": "user", "content": prompt}],
        )
        self._try_record_usage("distill_opening")
        return reply

    # ── Auto-tagging ───────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count: Chinese text ~0.6 tokens per char."""
        return int(len(text) * 0.6)

    # ── Long-context distillation ──────────────────────────────────────

    def _distill_longcontext(self, text: str, character_name: str) -> CharacterCard:
        """整本蒸：全文 + DISTILL_PROMPT → 一次性调用 LLM 产出角色卡。"""
        try:
            schema_obj = CharacterCard.model_json_schema()
            schema_str = json.dumps(schema_obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            print(f"生成 CharacterCard JSON Schema 失败：{exc}")
            raise

        system_prompt = (
            DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str
        )
        user_content = (
            f"以下是完整的文本内容，请基于全文为「{character_name}」生成角色卡。\n\n{text}"
        )

        user_messages = [{"role": "user", "content": user_content}]
        reply, upstream_truncated = self._chat_accounted(system_prompt, user_messages, "整本蒸馏", "distill_longcontext")

        data = self._parse_json_with_retry(
            reply, system_prompt, user_messages,
            action_label="distill_longcontext", upstream_truncated=upstream_truncated,
        )
        try:
            return CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc

    def _distill_longcontext_stream(self, text: str, character_name: str):
        """整本蒸流式版 — 一次性 LLM 调用 + 流式 token + 思考模式。"""
        try:
            schema_obj = CharacterCard.model_json_schema()
            schema_str = json.dumps(schema_obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            print(f"生成 CharacterCard JSON Schema 失败：{exc}")
            raise

        system_prompt = (
            DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str
        )
        user_content = (
            f"以下是完整的文本内容，请基于全文为「{character_name}」生成角色卡。\n\n{text}"
        )

        yield {"status": "formatting"}
        tc = 0
        for token in self._llm.chat_stream(system_prompt, [{"role": "user", "content": user_content}], max_tokens=self.CARD_MAX_TOKENS):
            if token == "\x00THINKING\x00":
                continue
            yield token
            tc += 1
            if tc % 50 == 0:
                yield {"heartbeat": True}

        self._try_record_usage("distill_longcontext")

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
            self._try_record_usage("distill_autotag")
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

    def _run_map_with_client(
        self,
        chunks: list[str],
        build_prompt: _cabc.Callable[[str], tuple[str, str]],
        usage_action: str,
        on_chunk_done: "callable | None" = None,
    ) -> tuple[list[tuple[int, str]], list[tuple[int, Exception]]]:
        """建 async client → 跑 Map → 关掉它。**同步侧调 Map 的唯一姿势。**

        「建 client / 跑 / 关」这一段蒸馏与识别都要，而清理失败的语义必须一致
        （关闭出错不改判已成功的主结果、不上抛、只记日志 —— 理由见
        `_run_async_in_ctx_thread` 的 docstring）：两份复制迟早漂移，收在这里。
        """
        async def _run():
            run_client = self._llm._make_async_client()
            try:
                return await self._run_map_concurrent(
                    chunks, build_prompt, usage_action, on_chunk_done, client=run_client,
                )
            finally:
                try:
                    await run_client.close()
                except Exception as exc:
                    print(f"[distiller] Map client close failed (non-fatal): "
                          f"{type(exc).__name__}: {exc}")

        return _run_async_in_ctx_thread(_run)

    async def _run_map_concurrent(
        self,
        chunks: list[str],
        build_prompt: _cabc.Callable[[str], tuple[str, str]],
        usage_action: str,
        on_chunk_done: "callable | None" = None,
        client: AsyncOpenAI | None = None,
    ) -> tuple[list[tuple[int, str]], list[tuple[int, Exception]]]:
        """Core Map — concurrent chunk analysis shared by sync and stream.

        ``build_prompt(chunk) -> (system, user)`` 由调用方给：蒸馏是「收集某角色的
        人格证据」，识别是「列出全书角色名单」，提示词不同、并发与记账骨架相同。
        ``usage_action`` 是整阶段汇总落账的 action 名。

        Returns (ordered [(index, analysis_text), ...], [(index, exception), ...]).
        ``on_chunk_done(index, result)`` is called synchronously within the
        async loop each time a chunk finishes.
        """
        sem = asyncio.Semaphore(self._map_concurrency)
        done_count = [0]
        lock = asyncio.Lock()
        failures: list[tuple[int, Exception]] = []
        usages: list[dict | None] = []

        async def _one(i: int, chunk: str) -> tuple[int, str]:
            async with sem:
                system, user = build_prompt(chunk)
                usage = None
                try:
                    result, usage = await self._llm.async_chat(
                        system, [{"role": "user", "content": user}], client=client
                    )
                except Exception as exc:
                    print(f"[distiller] Map chunk {i} failed: {exc}")
                    # 失败分片照样烧了 token（重试墙下空烧 26–100s）—— prompt 侧按字符
                    # 估算补记，completion 未知记 0 并标 estimated。只记成功 = 统计系统性偏低。
                    usage = estimate_usage_from_chars(len(system) + len(user))
                    async with lock:
                        failures.append((i, exc))
                    result = ""
            async with lock:
                done_count[0] += 1
                usages.append(usage)
            if on_chunk_done:
                on_chunk_done(i, result)
            return (i, result)

        tasks = [asyncio.create_task(_one(i, c)) for i, c in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        # Map 是 MapReduce 里最烧 token 的一段：整阶段汇总一条 + 调用次数（不是分片数 ——
        # 续跑命中/重试会让二者不一致，chunk_count 数的是真的调了几次）。
        merged = aggregate_usage(usages, len(usages))
        if merged is not None:
            self._try_record_usage(usage_action, merged)
        return results, failures

    async def _run_reduce_concurrent(
        self,
        batches: list[list[str]],
        character_name: str,
        on_batch_done: "callable | None" = None,
        sem_size: int = 6,
        client: AsyncOpenAI | None = None,
    ) -> list[tuple[int, str]]:
        """Run multiple Reduce batches concurrently with a semaphore."""
        sem = asyncio.Semaphore(sem_size)
        done_count = [0]
        lock = asyncio.Lock()

        async def _one(i: int, batch: list[str]) -> tuple[int, str]:
            async with sem:
                try:
                    result = await self._single_reduce_async(batch, character_name, client=client)
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
        results = await asyncio.gather(*tasks)
        # Reduce 全空 = 100% 失败，没有 Map 那种「按失败率容忍」的余地：把空列表当合法输入
        # 交给归并，模型会对零条分析**凭空产出**一份非空档案，下游「输出为空即失败」那道门
        # 结构上拦不住（缺陷 34）—— 判据要落在**归并的输入有没有内容**上。
        # 守卫放在此处，因为两条 reduce 路径（stream 的 `_reduce_batches` / 非 stream 的
        # `_do_reduce`）的批次结果都汇到这一个出口；放在调用点则会漏掉 `_do_reduce` 的递归那一路。
        if not any(r[1].strip() for r in results):
            raise DistillError(
                "蒸馏失败：归并阶段未能产出有效内容，请稍后重试",
                f"Reduce batch 全空：{len(batches)}/{len(batches)} 失败",
            )
        return results

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

    async def _single_reduce_async(self, raw_analyses: list[str], character_name: str, client: AsyncOpenAI | None = None) -> str:
        """Merge independent chunk analyses into a single profile (async, for concurrent batches)."""
        combined = self._reduce_user_prompt(raw_analyses, character_name)
        result, usage = await self._llm.async_chat(
            self._reduce_system_prompt(character_name),
            [{"role": "user", "content": combined}],
            client=client,
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
            run_client = self._llm._make_async_client()
            try:
                results = await self._run_reduce_concurrent(batches, character_name, client=run_client)
                return [r[1] for r in sorted(results, key=lambda x: x[0]) if r[1].strip()]
            finally:
                await run_client.close()

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

        # ── Long-context routing ─────────────────────────────────────────
        estimated = self._estimate_tokens(text)
        print(f"[distiller] Token estimate: ~{estimated} (threshold: {self._longctx_threshold})")
        if estimated < self._longctx_threshold:
            if is_chat:
                preprocessor = ChatPreprocessor()
                text = preprocessor._layer2_character_context(text, character_name)
            if on_progress:
                on_progress(0, 1)
            card = self._distill_longcontext(text, character_name)
            if on_progress:
                on_progress(1, 1)
            return card
        # ── End long-context routing ──────────────────────────────────────

        # 参数分档：classic类型用更大的profile
        chunk_size = self.effective_chunk_size(text_type)
        max_profile_len = self._max_profile_len
        if is_classic:
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
        map_system_fn = self._map_system_prompt_chat if is_chat else self._map_system_prompt
        map_user_fn = self._map_user_prompt_chat if is_chat else self._map_user_prompt

        def _build_map_prompt(chunk: str) -> tuple[str, str]:
            return map_system_fn(character_name), map_user_fn(chunk, character_name)

        try:
            map_results, map_failures = self._run_map_with_client(
                relevant, _build_map_prompt, "distill_map", _on_done,
            )
        except Exception as exc:
            # 上抛形态保持不变（RuntimeError + 同一句文案）：按类型/文案分流的地方
            # 不受这次重构影响。
            raise RuntimeError(f"Map 阶段失败：{exc}") from exc

        if on_progress:
            on_progress(total, total)

        # Failure rate check: if >50% chunks failed, bail with clear error
        total_chunks = len(relevant)
        failed = len(map_failures)
        if _map_failure_exceeds_tolerance(failed, total_chunks):
            err_text = str(map_failures[-1][1])
            if "rate limited (429)" in err_text or "429" in err_text:
                raise DistillError(
                    "蒸馏失败：上游接口限流，请稍后重试",
                    f"API 429；{failed}/{total_chunks} 个分片失败",
                )
            raise DistillError(
                "蒸馏失败：部分片段处理失败，请重试",
                f"{failed}/{total_chunks} 个分片失败；最后错误：{map_failures[-1][1]}",
            )
        if failed > 0:
            print(f"[distiller] {failed}/{total_chunks} map chunks failed (within tolerance), continuing")

        raw_analyses = [
            r[1] for r in map_results if r[1].strip() and r[1].strip() != "无"
        ]
        if not raw_analyses:
            raise DistillError("蒸馏失败：未能从任何片段中提取到角色信息")

        # Phase 2: Reduce — auto-batching merge
        profile_draft = self._do_reduce(raw_analyses, character_name)
        if not profile_draft.strip():
            raise DistillError("蒸馏失败：未能从文本中提取到角色信息")

        # Compress if needed
        if len(profile_draft) > max_profile_len:
            compress_system = (
                f"压缩以下「{character_name}」的角色档案到{max_profile_len}字以内。\n"
                "优先级：原文对话原句 > 行为证据 > 性格总结 > 背景信息。\n"
                "口癖和说话风格的原文例句必须保留，这是最重要的。\n"
                "合并重复信息，但不要删除矛盾点。"
            )
            compress_user = f"请压缩到{max_profile_len}字以内：\n\n{profile_draft}"
            # 失败被吞（fails open）也要落账 —— 先备好估算值，成功再换成真实值。
            # 一条记录覆盖两条路径：两条记录会让「只摘一条」看不出缺口。
            compress_usage = estimate_usage_from_chars(len(compress_system) + len(compress_user))
            try:
                profile_draft = self._llm.chat(
                    compress_system, [{"role": "user", "content": compress_user}],
                )
                compress_usage = self._llm.last_usage or compress_usage
            except Exception as exc:
                print(f"[distiller] Profile compression failed: {exc}")
            self._try_record_usage("distill_compress", compress_usage)

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
        user_messages = [{"role": "user", "content":
            f"以下是关于「{character_name}」的完整分析档案，请严格按照JSON格式输出角色卡。\n"
            f"特别注意：\n"
            f"- catchphrases 必须是原文中的真实口癖，不要编造\n"
            f"- dialogue_examples 必须是原文对话，不要改写\n"
            f"- personality_traits 每条必须附带具体场景证据\n\n"
            f"{profile_draft}"
        }]
        reply, upstream_truncated = self._chat_accounted(system_prompt, user_messages, "最终格式化", "distill_format")

        data = self._parse_json_with_retry(
            reply, system_prompt, user_messages,
            action_label="distill_format", upstream_truncated=upstream_truncated,
        )
        try:
            card = CharacterCard.model_validate(data)
        except ValidationError as exc:
            print(f"Pydantic 校验 CharacterCard 失败：{exc}")
            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc

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
        on_chunk_done: "callable | None" = None,
        resume_candidates: dict[int, dict] | None = None,
    ):
        """MapReduce 流式蒸馏 — 实时推送进度 + 流式 JSON 生成。

        yield 值类型：
        - dict: 进度事件（status: analyzing / merging / formatting / error）
        - str:  Format 阶段的 token 片段（SSE 路由累积为 card JSON）

        text_type: 'story' (默认) 或 'chat' (聊天记录预处理+专用提示词)

        on_chunk_done(index, result, fingerprint): 每片 Map 完成即同步回调，供调用方
        逐片落库（distiller 自身不做 IO）。命中缓存的片不回调（已在库里）。指纹由
        distiller 算——只有它知道该片的原文。

        resume_candidates: {index: {"result", "fingerprint"}} 上一轮已落库的候选片。
        命中三重门（见 _resume_hit）则直接复用、不发 LLM 调用；默认 None 时整条 Map
        路径与不续跑时完全一致。长上下文单次路径无分片，候选不生效（整跑）。
        """
        is_chat = text_type == "chat"
        is_classic = text_type == "classic"

        # ── Long-context routing ─────────────────────────────────────────
        estimated = self._estimate_tokens(text)
        print(f"[distiller] Token estimate: ~{estimated} (threshold: {self._longctx_threshold})")
        if estimated < self._longctx_threshold:
            if is_chat:
                preprocessor = ChatPreprocessor()
                text = preprocessor._layer2_character_context(text, character_name)
            # 长上下文单次路径无分片 checkpoint，续跑候选无从对应：直接整跑。
            yield from self._distill_longcontext_stream(text, character_name)
            return
        # ── End long-context routing ──────────────────────────────────────

        # 参数分档：classic类型用更大的chunk和profile
        chunk_size = self.effective_chunk_size(text_type)

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
            run_client = self._llm._make_async_client()
            try:
                sem = asyncio.Semaphore(self._map_concurrency)
                done_count = [0]
                lock = asyncio.Lock()
                failures: list[tuple[int, Exception]] = []
                usages: list[dict | None] = []

                async def _one(i: int, chunk: str) -> tuple[int, str]:
                    cached = _resume_hit(i, chunk, resume_candidates)
                    if cached is not None:
                        # 命中：零 LLM 调用，缓存结果照常并入 map_results 供 reduce 消费
                        result, from_cache, checkpoint_ok = cached, True, True
                    else:
                        checkpoint_ok = True
                        async with sem:
                            system = map_system(character_name)
                            user = map_user(chunk, character_name)
                            usage = None
                            try:
                                result, usage = await self._llm.async_chat(
                                    system, [{"role": "user", "content": user}], client=run_client
                                )
                            except Exception as exc:
                                print(f"[distiller] Map chunk {i} failed: {exc}")
                                async with lock:
                                    failures.append((i, exc))
                                # 失败片不落 checkpoint：空串配一个合法指纹写进去，续跑时
                                # 只有 _resume_hit 的门 2（非空）拦得住它，而 ON CONFLICT
                                # DO NOTHING 会让那行永久占位——重跑成功也写不进去。
                                # 空串仍并入 map_results（统一 append，不特判），但这不是契约：
                                # raw_analyses 按「非空且 != 无」过滤它，失败率判断用的是
                                # map_failures —— 收或收不到都无观测差异，别据此写断言。
                                # 已知残留（非进展循环）：该片下轮无候选 → 重发 → 同参数下
                                # 可能再次失败 → 该片永不成功。兜底是本函数末尾的
                                # `failed / total_chunks > 0.5` 整批 bail 分支；50% 以下会带着
                                # 缺片继续产出。缓解手段是调 llm.max_tokens（LLM_MAX_TOKENS
                                # 环境变量）或减小 chunk_size，不在适配器层解决。
                                # 实测佐证：同一片两次调用一次 content=0 一次 content=2060，
                                # 是随机饿死而非确定性截断，故重发有概率成功、不是死循环。
                                result = ""
                                checkpoint_ok = False
                                # 失败片照样烧 token（重试墙下空烧 26–100s）——prompt 侧
                                # 按字符估算补记，completion 未知记 0 并标 estimated。
                                usage = estimate_usage_from_chars(len(system) + len(user))
                            async with lock:
                                usages.append(usage)
                        from_cache = False
                    async with lock:
                        done_count[0] += 1
                        current = done_count[0]
                    q.put(("chunk", current, i, result, from_cache, checkpoint_ok))
                    return (i, result)

                tasks = [asyncio.create_task(_one(i, c)) for i, c in enumerate(relevant)]
                await asyncio.gather(*tasks)
                # 整阶段汇总一条：命中缓存的片零调用不进 usages，chunk_count 数的是真调用次数
                merged = aggregate_usage(usages, len(usages))
                if merged is not None:
                    self._try_record_usage("distill_map", merged)
                q.put(("done", failures))
            finally:
                await run_client.close()

        def _thread_run() -> None:
            try:
                asyncio.run(_map_with_progress())
            except Exception as exc:
                # 传异常本体而非 str(exc)：这一支的下游是**上屏**（下面的 yield），
                # 上屏文案必须经 user_facing_error 收敛，str() 会把内部标识带出去。
                q.put(("error", exc, None, None))

        t = C.ctx_thread(_thread_run, daemon=True)  # context 传播点
        t.start()

        map_results: list[tuple[int, str]] = []
        map_failures: list[tuple[int, Exception]] = []
        while True:
            item = q.get()
            kind = item[0]
            if kind == "done":
                map_failures = item[1]
                break
            if kind == "error":
                # 阶段名（Map/Reduce）是内部实现词，只进日志不上屏
                print(f"[distiller] map stage aborted: {item[1]}")
                yield {"error": user_facing_error(item[1])}
                return
            if kind == "chunk":
                _k, _current, idx, result, from_cache, checkpoint_ok = item
                map_results.append((idx, result))
                # 每片完成即回调落库（不攒批：攒批时 OOM 会丢掉一整批已付费的结果）。
                # 缓存命中的片已在库里，不重复写。指纹在此算——只有这里能拿到 relevant[idx] 的原文。
                # 失败片（checkpoint_ok=False）不回调：写进去的是空串 + 合法指纹，
                # 续跑时只有门 2 拦得住，且 ON CONFLICT DO NOTHING 让那行永久占位。
                if on_chunk_done and not from_cache:
                    if checkpoint_ok:
                        on_chunk_done(idx, result, text_fingerprint(relevant[idx]))
                    else:
                        # 静默的 checkpoint 失效是最贵的那种：点名该片本轮不入库、下轮重跑
                        print(f"[distiller] Chunk {idx} not checkpointed (Map failed); "
                              f"resume will re-run it")
                yield {"status": "analyzing", "current": item[1], "total": total}

        t.join(timeout=5)

        map_results.sort(key=lambda x: x[0])

        # Failure rate check: if >50% chunks failed, bail
        total_chunks = len(relevant)
        failed = len(map_failures)
        if _map_failure_exceeds_tolerance(failed, total_chunks):
            err_text = str(map_failures[-1][1])
            if "rate limited (429)" in err_text or "429" in err_text:
                print(f"[distiller] aborting stream: API 429；{failed}/{total_chunks} 个分片失败")
                yield {"error": "蒸馏失败：上游接口限流，请稍后重试"}
            else:
                print(f"[distiller] aborting stream: {failed}/{total_chunks} 个分片失败；"
                      f"最后错误：{map_failures[-1][1]}")
                yield {"error": "蒸馏失败：部分片段处理失败，请重试"}
            return
        if failed > 0:
            print(f"[distiller] {failed}/{total_chunks} map chunks failed (within tolerance), continuing")

        raw_analyses = [
            r[1] for r in map_results if r[1].strip() and r[1].strip() != "无"
        ]

        if not raw_analyses:
            yield {"error": "未能从任何片段中提取到角色信息"}
            return

        # ── Phase 2: Reduce — streaming with auto-batching ──
        # 续跑只缓存 Map 片：Reduce 每次都整跑。这是权衡不是遗漏——分批续跑最多省最后
        # 一两次调用，却要定义「部分 reduce 结果如何合并」的一致性语义，得不偿失。
        # 缓存命中的 Map 片已并入 map_results，reduce 天然看到全集。
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
                run_client = self._llm._make_async_client()
                try:
                    return await self._run_reduce_concurrent(
                        batches, character_name, _on_batch_done, client=run_client
                    )
                finally:
                    await run_client.close()

            def _reduce_thread() -> None:
                try:
                    asyncio.run(_reduce_batches())
                    rq.put(("done",))
                except Exception as exc:
                    rq.put(("error", exc))   # 同上：下游是上屏，传本体不传 str()

            rt = C.ctx_thread(_reduce_thread, daemon=True)  # context 传播点
            rt.start()

            batch_by_index: dict[int, str] = {}
            while True:
                item = rq.get()
                kind = item[0]
                if kind == "done":
                    break
                if kind == "error":
                    print(f"[distiller] reduce stage aborted: {item[1]}")
                    yield {"error": user_facing_error(item[1])}
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
            max_tokens=self.CARD_MAX_TOKENS,
        )
        self._try_record_usage("distill_format")
