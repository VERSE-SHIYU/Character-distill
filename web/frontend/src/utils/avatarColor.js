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

const ANGLES = [120, 135, 150, 165]

// 无头像占位表面：分层"珍珠"渐变 —— 基础色相渐变 + 左上柔光 + 右下暗影，
// 让平面色块变成有光照的绸缎感；角度随名字哈希微调，同调色板不雷同。
export function avatarGradient(name) {
  let h = 0
  for (const ch of String(name || '?')) h = (h * 31 + ch.codePointAt(0)) >>> 0
  const [c1, c2] = PALETTE[h % PALETTE.length]
  const angle = ANGLES[(h >> 3) % ANGLES.length]
  return [
    'radial-gradient(120% 90% at 18% 12%, rgba(255,255,255,0.34) 0%, rgba(255,255,255,0) 46%)',
    'radial-gradient(115% 115% at 85% 95%, rgba(0,0,0,0.22) 0%, rgba(0,0,0,0) 55%)',
    `linear-gradient(${angle}deg, ${c1} 0%, ${c2} 100%)`,
  ].join(', ')
}
