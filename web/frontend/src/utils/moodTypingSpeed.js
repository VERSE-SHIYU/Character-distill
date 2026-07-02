import { CHAR_INTERVAL_MS } from '../hooks/useTypewriter'

const MOOD_SPEED = [
  { keywords: ['生气', '愤怒', '急躁'], coeff: 0.55 },
  { keywords: ['开心', '兴奋'], coeff: 0.75 },
  { keywords: ['平静', '平和', '淡定'], coeff: 1.0 },
  { keywords: ['警惕', '戒备', '紧张'], coeff: 1.2 },
  { keywords: ['低落', '难过', '伤心'], coeff: 1.35 },
  { keywords: ['慵懒', '困倦', '疲惫', '累'], coeff: 1.5 },
]

function _moodCoeff(mood) {
  if (!mood) return 1.0
  const low = mood.toLowerCase()
  for (const entry of MOOD_SPEED) {
    if (entry.keywords.some((k) => low.includes(k))) return entry.coeff
  }
  return 1.0
}

export function moodCharInterval(mood) {
  const coeff = _moodCoeff(mood)
  return Math.round(CHAR_INTERVAL_MS * coeff)
}
