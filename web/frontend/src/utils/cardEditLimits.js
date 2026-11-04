// 上限只限制用户新输入（输入框的 maxLength、列表的添加按钮）；已有内容照常显示与保存，保存不检查上限。
export const FIELD_MAX = {
  identity: 80,
  personality_traits: 600,
  tone: 90,
  sentence_pattern: 100,
  catchphrases: 240,
  vocabulary_level: 40,
  taboo_words: 120,
  values: 180,
  key_memories: 800,
  inner_tensions: 500,
  background: 300,
  first_message: 300,
  emotional_patterns: 550,
  decision_style: 180,
  arc_axis: 40,
  dialogue_examples: 600,
}

export const REL_MAX = 8
export const ARC_MAX = 5
export const BEHAVIOR_MAX = 12        // 从头到尾都成立的做法
export const PHASE_BEHAVIOR_MAX = 6   // 每个阶段专属的做法
