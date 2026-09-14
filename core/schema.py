"""角色卡与相关结构的 Pydantic 模型定义。"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, model_validator

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

class Relationship(BaseModel):
    """人际关系"""
    target: str                  # 对方名字
    relation: str                # 关系类型
    attitude: str = ""           # 态度描述
    note: str = ""               # 注入用的单向口径：站在本角色视角，一句话讲清我和ta的关系/我怎么看ta

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
    character_arc: list[str] = []       # 角色弧线：故事中经历的成长变化阶段，每阶段一句话
    tags: list[str] = []                # AI 自动打的分类标签（蒸馏时填充）
    psyche: PsycheProfile = PsycheProfile()
    cognitive: CognitiveProfile = CognitiveProfile()  # 认知/语言画像
    awakening_message: str = ""  # 蒸馏完成时生成的苏醒台词


# ── Evidence：检索来源的结构化契约 ─────────────────────────────────────
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


class SceneMeta(TypedDict):
    """scene 类来源的解释字段。

    semantic / emotion_affinity 是**未加权的原始分量**，final 是加权和
    （``0.7·semantic + 0.3·emotion_affinity``）—— 三者并列才解释得了 final 从哪来；
    只留 final，可解释性就没了。
    """
    chapter: str | None       # 章节；今天无生产方（scene_indexer 只写 emotion/characters/scene_index），恒 None
    chunk_id: str | None      # 块标识，取 chroma 元数据 scene_index
    semantic: float           # 语义相似度原始分量 0-1
    emotion_affinity: float   # 情感匹配原始分量 0-1
    final: float              # 加权总分 0-1


class MemoryMeta(TypedDict):
    """memory 类来源的解释字段（memory_manager.search 已在算：见其返回的 memory_mood）。"""
    relevance: float
    importance: int
    age_seconds: float
    mood: str


class WebMeta(TypedDict):
    """web 类来源的解释字段。"""
    url: str
    source: str
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
