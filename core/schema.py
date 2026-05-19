"""角色卡与相关结构的 Pydantic 模型定义。"""

from pydantic import BaseModel


class SpeakingStyle(BaseModel):
    """说话风格"""
    tone: str                    # 整体语气，如"冷淡""热情""讽刺"
    sentence_pattern: str        # 句式特征，如"短句为主""喜欢反问"
    catchphrases: list[str]      # 口癖，2-5个
    vocabulary_level: str        # 用词水平，如"文雅""粗俗""学术"
    taboo_words: list[str]       # 绝不会说的话

class Relationship(BaseModel):
    """人际关系"""
    target: str                  # 对方名字
    relation: str                # 关系类型
    attitude: str = ""           # 态度描述

class CharacterCard(BaseModel):
    """角色卡——蒸馏引擎的唯一输出格式"""
    name: str
    identity: str                # 一句话身份
    personality_traits: list[str]  # 3-5个，每个带原文依据
    speaking_style: SpeakingStyle
    values: list[str]            # 2-4个核心价值观
    key_memories: list[str]      # 3-5个关键经历
    relationships: list[Relationship]
    inner_tensions: list[str]    # 1-3个内在矛盾
    background: str              # 背景摘要
    first_message: str = ""      # 角色开场白
    dialogue_examples: list[str] = []   # 2-3轮原文对话示例，体现角色说话风格
    emotional_patterns: list[str] = []  # 情感模式：什么情况下会生气/开心/沉默/逃避
    decision_style: str = ""            # 决策风格：冲动型/谨慎型/情感驱动/逻辑驱动
