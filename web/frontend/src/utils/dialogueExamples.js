// 对话示例在编辑表单里的形态。纯函数，无 React。
//
// 存卡的形态是字符串数组，一个元素一组（组内「对方一句」「角色一句」各占一行）；表单里是一段
// 文本，组与组之间空一行。拆与拼只在这里定义 —— 按「行」拆会把一组拆成两条，而聊天时只取
// 前 3 条（core/context_engine.py），示例就散了。
//
// 示例有两个落点：卡的顶层（表单里那一栏），和各阶段下（有起点的卡，系统挑的示例按原文位置
// 归在阶段的 overlay 里；表单只显示、不编辑）。后者的取法也只在这里。

export function examplesToText(examples) {
  return Array.isArray(examples) ? examples.join('\n\n') : (examples || '')
}

// 各阶段下的对话示例：[{ index, label, examples }]，只列有示例的阶段，index 从 0 起。
export function examplesByPhase(card) {
  return (card?.character_arc?.phases || [])
    .map((phase, index) => ({ index, label: phase.label || '', examples: phase.overlay?.dialogue_examples || [] }))
    .filter((phase) => phase.examples.length > 0)
}

// 空行分组（空行里只有空白也算）；组内每行去掉首尾空白、丢掉空行。
export function textToExamples(text) {
  return String(text || '')
    .split(/\n\s*\n/)
    .map((group) => group.split('\n').map((line) => line.trim()).filter(Boolean).join('\n'))
    .filter(Boolean)
}
