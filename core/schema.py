"""角色卡与相关结构的 Pydantic 模型定义。"""

import logging
import json
from typing import Any, Literal, NamedTuple, TypedDict

from pydantic import BaseModel, model_validator

logger = logging.getLogger(__name__)

# 预设标签列表 — 用于角色分类和 AI 自动打标
PRESET_TAGS = [
    "恋爱", "动漫", "游戏", "治愈", "悬疑", "古风", "校园",
    "奇幻", "科幻", "日常", "虐心", "搞笑", "男频", "女频", "原创",
]


class SpeakingStyle(BaseModel):
    """说话风格"""
    tone: str = ""                    # 整体语气，如"冷淡""热情""讽刺"
    sentence_pattern: str = ""        # 句式特征，如"短句为主""喜欢反问"
    catchphrases: list[str] = []      # 口癖，2-5个
    vocabulary_level: str = ""        # 用词水平，如"文雅""粗俗""学术"
    taboo_words: list[str] = []       # 绝不会说的话

class PhaseAttitude(BaseModel):
    """关系在某一阶段的态度。投影到阶段 k 时，取 phase ≤ k 里最新的一条。"""
    phase: int
    attitude: str = ""


class Relationship(BaseModel):
    """人际关系"""
    target: str                  # 对方名字
    relation: str                # 关系类型
    attitude: str = ""           # 态度描述
    note: str = ""               # 注入用的单向口径：站在本角色视角，一句话讲清我和ta的关系/我怎么看ta
    phase_attitudes: list[PhaseAttitude] = []  # 各阶段的态度；投影时按阶段取 ≤k 的最新一条

class ChatSession(BaseModel):
    """对话会话元数据（P5 预留，暂不接入逻辑）"""
    affinity_score: int = 50  # 好感度 0-100，默认 50 中立


class CognitiveProfile(BaseModel):
    """认知/语言画像：压制 LLM 通用博士腔，确保角色说话合身份。

    education_level — 文化程度，如 文盲/识字不多/普通/受过良好教育/学者
    knowledge_scope — 知识边界（时代/阶层/见识决定知道什么不知道什么）
    speech_style — 说话腔调（用词雅俗、长短句、成语/专业词、口头禅、方言感）
    vocabulary_level — 用词层次: 粗白/日常/文雅/书面
    """
    education_level: str = "普通"
    knowledge_scope: str = ""
    speech_style: str = ""
    vocabulary_level: str = "日常"


class PsycheProfile(BaseModel):
    """心理画像：大五人格 + 情感动力学参数，作为 set-point 基线和角色推理的统一数据源。"""
    # 大五人格（1-5 离散档，依据 PsyPlay arXiv:2502.03821）
    openness: int = 3
    conscientiousness: int = 3
    extraversion: int = 3
    agreeableness: int = 3
    neuroticism: int = 3
    # 情感动力学（依据 Kuppens 情感动力学 — baseline/variability/inertia）
    affinity_baseline: int = 50      # 关系基线起点 0-100
    volatility: str = "适中"          # 波动幅度: 平稳/适中/剧烈
    grudge_inertia: str = "一般"      # 负面消化速度: 大度/一般/记仇
    # 推理锚点
    triggers: list[str] = []         # 雷点：碰了就炸的具体点
    soft_spots: list[str] = []       # 软肋：戳中会心软的点


class BehaviorCore(BaseModel):
    """情境→行为：此人遇到某类情境时的具体做法。

    存卡（`SituationBehavior`）与给模型的草稿（`core.card_draft.DraftBehavior`）共用的底：
    两边都只写一次情境与做法，各自的附加字段（存卡的摘录、草稿的阶段标注）在子类上加。
    """
    situation: str           # 一类情境（不是某一章的某件事）
    behavior: str            # 此人在这类情境下的具体做法


class SituationBehavior(BehaviorCore):
    """情境→行为：此人遇到某类情境时的具体做法。

    挂在哪里就是它的适用范围：阶段下的（`ArcPhase.behaviors`）只在那个阶段成立，
    卡片顶层的（`CharacterCard.situation_behaviors`）从头到尾都成立。挂到哪里不由模型
    选，由 `core.card_draft.card_from_draft` 按模型标的阶段分发。
    """
    source_quote: str = ""   # 原文摘录，只用于展示；落卡前核对，查不到即清空


# 弧线的两层各有一个「共用的底」：存卡（`ArcPhase` / `CharacterArc`）与给模型的草稿
# （`core.card_draft.DraftArc`）只差阶段下挂不挂做法，其余字段与旧形态的容错都在底上，
# 两边写一次。docstring 会进发给模型的 JSON 结构，只写给模型看的话。
class PhaseState(BaseModel):
    """弧线上的一个阶段：是什么时候、什么心态。"""
    label: str = ""   # 这一阶段的心态/立场（≤8 字）；旧卡转来的为空
    state: str = ""   # 这一阶段的状态，一句话，句首点明对应的故事时期

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_string(cls, value: Any) -> Any:
        """旧卡的阶段是一句字符串 → 当作没有 label 的 state。"""
        return {"state": value} if isinstance(value, str) else value


class BoundaryExample(BaseModel):
    """边界示范：此阶段不可能知道的提问 + 该阶段的回应。只学应对方式，不照抄措辞。"""
    ask: str = ""
    reply: str = ""


class ArcPhase(PhaseState):
    """弧线上的一个阶段。"""
    behaviors: list[SituationBehavior] = []   # 只在这一阶段成立的做法
    memories: list[str] = []                  # 只在这一阶段成立的关键经历
    start: int | None = None                  # 本阶段起点在**规范化原文**中的位置
    boundary_examples: list[BoundaryExample] = []  # 只在这一阶段成立的边界示范
    dialogue_examples: list[str] = []         # 属于这一阶段的原文对话示例


class ArcAxis(BaseModel):
    """角色弧线的变化轴；阶段列表由子类按各自的阶段形态声明。"""
    axis: str = ""                   # 这条弧线变的是什么，一句「从…到…」；旧卡为空

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_list(cls, value: Any) -> Any:
        """旧卡的弧线是阶段列表 → 当作没有 axis 的 phases。"""
        return {"phases": value} if isinstance(value, list) else value


class CharacterArc(ArcAxis):
    """角色弧线：一条变化轴 + 按故事顺序排列的阶段（「变的部分」）。"""
    phases: list[ArcPhase] = []
    source_fingerprint: str = ""   # 阶段起点所依据的正文指纹（§3.4 坐标的前提）


class CharacterCard(BaseModel):
    """角色卡——蒸馏引擎的唯一输出格式"""
    name: str
    identity: str = ""                # 一句话身份
    personality_traits: list[str] = []  # 3-5个，每个带原文依据
    speaking_style: SpeakingStyle = SpeakingStyle(tone="", sentence_pattern="", catchphrases=[], vocabulary_level="", taboo_words=[])
    values: list[str] = []            # 2-4个核心价值观
    key_memories: list[str] = []      # 3-5个关键经历
    relationships: list[Relationship] = []
    inner_tensions: list[str] = []    # 1-3个内在矛盾
    background: str = ""              # 背景摘要
    first_message: str = ""      # 角色开场白
    dialogue_examples: list[str] = []   # 2-3轮原文对话示例，体现角色说话风格
    emotional_patterns: list[str] = []  # 情感模式：什么情况下会生气/开心/沉默/逃避
    decision_style: str = ""            # 决策风格：冲动型/谨慎型/情感驱动/逻辑驱动
    character_arc: CharacterArc = CharacterArc()  # 角色弧线：变化轴 + 阶段
    situation_behaviors: list[SituationBehavior] = []  # 情境→行为：从头到尾都成立的做法
    tags: list[str] = []                # AI 自动打的分类标签（蒸馏时填充）
    psyche: PsycheProfile = PsycheProfile()
    cognitive: CognitiveProfile = CognitiveProfile()  # 认知/语言画像
    awakening_message: str = ""  # 蒸馏完成时生成的苏醒台词


# ── 格式化字段分组（WP7）──────────────────────────────────────────────
# 流式蒸馏的格式化按组并行生成；非流式 distill_incremental 仍用完整提示词。
# 这里是分组的**唯一出处**：提示词片段、子 schema 都从这里派生，不另存副本
# （两份手维护清单必然漂移）。F3 断言「各组 ∪ POST_FORMAT_FIELDS ==
# CharacterCard.model_fields，两两无交集」。组序即提示词片段顺序，别随意调。
FORMAT_GROUPS: dict[str, tuple[str, ...]] = {
    "G1": ("name", "identity", "background"),
    "G2": ("personality_traits", "values", "inner_tensions",
           "emotional_patterns", "decision_style"),
    "G3": ("speaking_style", "first_message", "cognitive"),
    # relationships 从 G4 拆出来单独成组：关系条数随登场人数增长（宝玉这种主角几十
    # 条），与其余字段同组会把这组的输出顶到 token 上限；分组之间各给各的上限，
    # 拆开后关系再长也只挤自己那一组。
    "G4": ("psyche",),
    "G5": ("relationships",),
    # 弧线与情境→行为、关键记忆同组：做法/记忆标的是阶段编号，必须和阶段在同一次调用
    # 里产出（key_memories 从 G4 挪来这里，D8/本段 §3.2）。单独成组的理由同 G5。
    "G6": ("character_arc", "situation_behaviors", "key_memories"),
}

# 后置步骤产出的字段（_auto_tag / _generate_awakening / 挑选对话示例），不进分组。
POST_FORMAT_FIELDS: tuple[str, ...] = ("tags", "awakening_message", "dialogue_examples")


# ── Evidence：检索来源的结构化契约 ─────────────────────────────────────
# 命名消歧：仓里 `evidence` 一词已被 `docs/evidence/` + `evidence_writer` +
# `test_evidence_integrity` 占用（审计探针的产物）。此处 `EvidenceItem` 指
# **检索来源**（剧情原文 / 记忆 / 网络），与那套审计产物无关，别混淆。
#
# 「检索来源」面板的数据契约：**只描述检索层产出了什么**，不描述下游怎么渲染 ——
# 这里不许出现「折叠 / 卡片 / 图标 / 颜色」这类前端概念（依赖倒置：契约不依赖渲染）。
#
# 为什么 meta 是 dict 而非把三类字段摊平进 EvidenceItem：三类来源的字段各自演化
# （scene 谈心理解释、memory 谈记忆强度、web 谈出处），摊平会让每个 kind 都拖着
# 一堆恒为 None 的键。代价是 dict 天生可以「随便塞」—— 所以每个 kind 的键集在下面
# 用 TypedDict 显式声明，并由 EvidenceItem 的运行期校验门强制（不是只写在注释里）。
#
# 选 TypedDict 而非校验函数的理由：声明的键集本身可被机器读（``__annotations__``），
# 于是「声明」与「校验」同源 —— 校验函数要自带一份键名副本，那是第二份手维护清单，
# 两份必然漂移（本仓缺陷 21 / 25 的形态）。

EvidenceKind = Literal["scene", "memory", "web"]

# 一次检索调用对外的四态。**timeout 只由工具执行器产生**（``AgentToolkit.execute`` 的
# ``fut.result(timeout)`` 弃船路径）——「这次调用没在预算内回来」是调用层事实，不是
# 检索本体说了什么。故引擎侧的 ``RetrievalStatus`` 只有三态：把 timeout 塞进那里，等于
# 让引擎的类型承诺一个它永远产不出的值（死分支，本仓缺陷 27 同型）。两者的包含关系由
# ``tests/test_agent_evidence.py`` 的漂移锁钉住。
SourceStatus = Literal["hit", "empty", "failed", "timeout"]


class SceneMeta(TypedDict):
    """scene 类来源的解释字段。

    semantic / emotion_affinity 是**未加权的原始分量**，final 是加权和
    （``0.7·semantic + 0.3·emotion_affinity``）—— 三者并列才解释得了 final 从哪来；
    只留 final，可解释性就没了。

    **``semantic`` 是相对量，不是绝对相关度**：它按**本次结果集**的 max_dist 归一化
    （``1 - dist/max_dist``），跨查询不可比 —— 换一组候选，同一条原文的 semantic 就变了。
    且本次结果里**距离最大的那条恒为 0**，只命中一条时那条也恒为 0。故契约层不承诺
    这个数绝对可读，**下游只可用于排序**（前端直接绑这一条，别再自行解读）。
    """
    chapter: str | None       # 章节；今天无生产方（scene_indexer 只写 emotion/characters/scene_index），恒 None
    chunk_id: str | None      # 块标识，取 **chroma 返回的真 id**（如 scene_3），不取 metadata 影子副本
    semantic: float           # 语义相似度原始分量 0-1（本次结果集内归一化，跨查询不可比）
    emotion_affinity: float   # 情感匹配原始分量 0-1
    final: float              # 加权总分 0-1


class MemoryMeta(TypedDict):
    """memory 类来源的解释字段。

    与 scene 同一条规矩：**分量与合成分并列存**，不只留合成分。memory_manager.search
    已算出这几个量（``emo_affinity`` / ``final`` / ``relevance``），接口处只用映射，
    不许重算。（其返回里情绪键叫 ``memory_mood``，此处契约为 ``mood``。）

    也正因 final 不落在 0-1，``EvidenceItem.score`` 对本类恒为 None（见
    ``memory_evidence`` 的说明）—— 要排序就读这里的 ``final``。
    """
    relevance: float
    importance: int
    age_seconds: float
    mood: str
    emo_affinity: float       # 情感加成原始分量
    final: float              # base × (1 + 0.25·emo_affinity)，上界 1.25 —— **不是 0-1**


class WebMeta(TypedDict):
    """web 类来源的解释字段。

    ``url`` / ``source`` 可以为 None —— 检索到的片段未必带链接（DDG 的 RelatedTopics
    条目可能没有 FirstURL）。**None 与 "" 是两回事**：取不到就如实 None，不许填空串
    假装有。``url=None`` 时前端**不得**把它渲染成空链接或死链（commit 5 直接绑这条）。
    """
    url: str | None
    source: str | None
    fetched_at: str


# kind → 键集契约的唯一出处：EvidenceItem 的运行期校验门与测试都从这里读，不另存副本
EVIDENCE_META: dict[str, Any] = {
    "scene": SceneMeta,
    "memory": MemoryMeta,
    "web": WebMeta,
}


class EvidenceItem(BaseModel):
    """一条检索来源（原文 / 记忆 / 网络）—— 只宣称「检索到了什么」。

    **不宣称「模型据此生成」**：本仓 judge 证伪（κ≈0）与 arXiv 2606.28358 的激活
    修补实验同向 —— 内联引用会在答案并未真正基于来源时制造可验证的假象。故字段
    只描述来源本身，措辞一律「检索来源」，不用「依据」「引用」。

    Attributes:
        kind: 来源类别。三种就是三种，不留第四种的扩展点。
        text: 命中原文（交给下游渲染的那段字）。
        score: 归一化到 0-1 的相关度；该类没有可比分数时为 None。
        meta: 该 kind 专属的解释字段，键集由 ``EVIDENCE_META[kind]`` 声明并在此强制。
    """
    kind: EvidenceKind
    text: str
    score: float | None = None
    meta: dict[str, Any]

    @model_validator(mode="after")
    def _meta_keys_match_contract(self) -> "EvidenceItem":
        declared = set(EVIDENCE_META[self.kind].__annotations__)
        actual = set(self.meta)
        if actual != declared:
            raise ValueError(
                f"{self.kind} 的 meta 键集与契约不符：多={sorted(actual - declared)} "
                f"少={sorted(declared - actual)}（契约见 EVIDENCE_META[{self.kind!r}]）"
            )
        return self


class RetrievalWindow(NamedTuple):
    """检索时间窗：只取规范化坐标 `npos < before` 的片段，且只对讲同一份正文（指纹相符）、
    带位置（`pos_schema`）的集合生效。由 `core/arc_view` 产出，`RAGEngine` 一处消费。"""
    before: int
    source_fingerprint: str


class SourceTrace(NamedTuple):
    """一次检索调用的可追溯记录 —— agent 工具与直调两条路径共用这一个形状。

    **只带 items，不带 block**：block 是喂给模型的字符串（web 的那份还是 LLM 改写
    结果），带出去会让下游把「改写结果」当成「检索来源」—— 本线根本前提是
    items ≠ prompt。block 留在 ``RetrievalResult`` 里，跨到 chat 层时被 ``trace()``
    剥掉。
    """
    source: EvidenceKind
    status: SourceStatus
    items: list[EvidenceItem]


# ── 持久化投影：SourceTrace ⇄ 落库文本 ──────────────────────────────────
# 落库形状**不是** SourceTrace 的别称，是另一个形状，理由两条：
#
# 1. ``text`` 落库的是**截断预览**，不是原文（全文入库会撑大消息表：一条 scene item
#    数百到上千字 × 每次检索 × 每条消息）。既然承诺不了原文，就不许长得像原文 ——
#    复用 ``EvidenceItem`` 会让下游把预览当原文读。故带 ``truncated`` 显式标记
#    （「不许静默截断」），且**不提供反向重建**：重建出 SourceTrace 等于把预览
#    重新伪装成检索事实。
# 2. ``kind`` 不进单条 item —— 父层 ``source`` 已经说了来源，同一事实不留两份
#    （本仓缺陷 25 的形态：一个字段的描述与另一个字段重复且无人强制一致）。
#
# ``meta`` 原样保留：它装着**引用**（scene 的 chunk_id / web 的 url）—— 那是将来按需
# 回查原文的句柄，而且体量是几个数，不是撑表的原因。

EVIDENCE_SNAPSHOT_CHARS = 300


class EvidenceSnapshotItem(TypedDict):
    """落库的一条来源快照。``text`` 是截断预览，``truncated`` 记它有没有被裁过。"""
    text: str
    truncated: bool
    score: float | None
    meta: dict[str, Any]


class EvidenceSnapshot(TypedDict):
    """一条 ``SourceTrace`` 的落库形态：``source``/``status``/``items`` 一一对应。"""
    source: EvidenceKind
    status: SourceStatus
    items: list[EvidenceSnapshotItem]


def evidence_snapshots(traces: list[SourceTrace]) -> list[EvidenceSnapshot] | None:
    """``SourceTrace`` 列表 → 快照列表。**空 → None**。

    不返回 ``[]``：``None`` 与 ``[]`` 是两种「无证据」，下游就得自己裁决该认哪个。
    只留 ``None`` 一种表示，「没证据」就只有一个形状。

    这是**唯一形状定义**：落库文本（``evidence_to_json``）、SSE 的 evidence 帧、
    历史/重逢接口读回来的证据，三处消费的都是本函数产出的形状 —— 前后端不许各拼一份
    （本仓缺陷 21 / 25 的教训：同一形状第二份定义，两份必然漂移）。
    """
    if not traces:
        return None
    return [
        {
            "source": t.source,
            "status": t.status,
            "items": [
                {
                    "text": it.text[:EVIDENCE_SNAPSHOT_CHARS],
                    "truncated": len(it.text) > EVIDENCE_SNAPSHOT_CHARS,
                    "score": it.score,
                    "meta": dict(it.meta),
                }
                for it in t.items
            ],
        }
        for t in traces
    ]


def evidence_to_json(traces: list[SourceTrace]) -> str | None:
    """``evidence_snapshots`` 的落库文本形态（``json.dumps``，空 → ``None`` → 列留 NULL）。"""
    snapshots = evidence_snapshots(traces)
    if snapshots is None:
        return None
    return json.dumps(snapshots, ensure_ascii=False)


def parse_evidence(raw: Any) -> list[EvidenceSnapshot] | None:
    """落库文本 → 快照列表。``None`` / 空串 → ``None``（**不造 ``[]``**）。

    **解析失败不抛**：一条坏行不该让整段会话读不出来（老消息与坏行都是「没有证据」这个
    对外语义，区别只在日志）。但不静默 —— 打一行点名「解析失败」的日志，与「本来就没有」
    可辨。代价如实记：库里其实有值、接口却是 None，只看接口会误读成「当时没检索」。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("[schema] evidence 解析失败，该条消息按「无证据」返回: %s", exc)
            return None
    if not isinstance(raw, list):
        logger.warning(
            "[schema] evidence 落库值不是 JSON 数组（%s），按「无证据」返回",
            type(raw).__name__,
        )
        return None
    return raw


# ── 构造入口：键名的唯一出处 ────────────────────────────────────────────
# 每个 kind 一个工厂，生产方不再手拼 meta dict（键名散在三处 = 将来第四个生产方照样
# 手拼一个）。工厂**只做两件事**：写键名、纯改名映射（如 memory_mood → mood）。
# 明令禁止：归一化、裁剪、缺失字段填默认 —— 那会让「编造」藏进工厂，且所有生产方一起
# 中招（谁在这里塞一句 ``score = min(final, 1.0)``，契约层就开始生产没有依据的数）。
# 缺字段就该 KeyError/TypeError 当场炸，不许静默补值。validator 退为兜底，防手拼绕过。

def scene_evidence(
    *,
    text: str,
    final: float,
    semantic: float,
    emotion_affinity: float,
    chunk_id: str | None,
    chapter: str | None,
) -> EvidenceItem:
    """scene 来源的构造入口（纯映射，不做任何计算）。"""
    return EvidenceItem(
        kind="scene",
        text=text,
        score=final,
        meta=SceneMeta(
            chapter=chapter,
            chunk_id=chunk_id,
            semantic=semantic,
            emotion_affinity=emotion_affinity,
            final=final,
        ),
    )


def memory_evidence(
    *,
    text: str,
    relevance: float,
    importance: int,
    age_seconds: float,
    memory_mood: str,
    emo_affinity: float,
    final: float,
) -> EvidenceItem:
    """memory 来源的构造入口。``memory_mood → mood`` 是纯改名。

    ``score`` 恒 None：memory 的 ``final = base × (1 + 0.25·emo_affinity)`` 上界 1.25，
    **不在 EvidenceItem.score 承诺的 0-1 内**，硬压/clamp 是编造契约没承诺的数。
    前端要排序就用 ``meta["final"]``（同类内可比），不要读 score。
    """
    return EvidenceItem(
        kind="memory",
        text=text,
        score=None,
        meta=MemoryMeta(
            relevance=relevance,
            importance=importance,
            age_seconds=age_seconds,
            mood=memory_mood,
            emo_affinity=emo_affinity,
            final=final,
        ),
    )


def web_evidence(
    *,
    text: str,
    url: str | None,
    source: str | None,
    fetched_at: str,
) -> EvidenceItem:
    """web 来源的构造入口。url/source 取不到就是 None，**不许填空串假装有**。"""
    return EvidenceItem(
        kind="web",
        text=text,
        score=None,
        meta=WebMeta(url=url, source=source, fetched_at=fetched_at),
    )
