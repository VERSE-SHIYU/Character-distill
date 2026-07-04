const PALETTE = [
  ['#8A7AA8', '#A895C4'], // 雾紫
  ['#5A7FB5', '#7396C6'], // 钴蓝
  ['#6FAD74', '#8BC490'], // 苔绿
  ['#C4956A', '#C99B6E'], // 暖铜
  ['#B8869B', '#CC9FB2'], // 玫瑰灰
  ['#5F9EA0', '#7AB8BA'], // 青灰
  ['#A88A5F', '#B29465'], // 沙金
  ['#7A8FA6', '#94A9BE'], // 石板蓝
]

export function avatarGradient(name) {
  let h = 0
  for (const ch of String(name || '?')) h = (h * 31 + ch.codePointAt(0)) >>> 0
  const [c1, c2] = PALETTE[h % PALETTE.length]
  return `linear-gradient(135deg, ${c1} 0%, ${c2} 100%)`
}
