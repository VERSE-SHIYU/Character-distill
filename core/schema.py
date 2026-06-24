"""角色卡与相关结构的 Pydantic 模型定义。"""

from pydantic import BaseModel

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
