// 对话示例在编辑表单里的文本形态。纯函数，无 React。
//
// 存卡的形态是字符串数组，一个元素一组（组内「对方一句」「角色一句」各占一行）；表单里是一段
// 文本，组与组之间空一行。拆与拼只在这里定义 —— 按「行」拆会把一组拆成两条，而聊天时只取
// 前 3 条（core/context_engine.py），示例就散了。

export function examplesToText(examples) {
  return Array.isArray(examples) ? examples.join('\n\n') : (examples || '')
}

// 空行分组（空行里只有空白也算）；组内每行去掉首尾空白、丢掉空行。
export function textToExamples(text) {
  return String(text || '')
    .split(/\n\s*\n/)
    .map((group) => group.split('\n').map((line) => line.trim()).filter(Boolean).join('\n'))
    .filter(Boolean)
}
