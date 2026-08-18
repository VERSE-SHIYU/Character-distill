// 无头像占位符（珍珠渐变 + 磨砂字母徽章）验证 — 首页"发现"区市场卡（真实数据）
// 用法: node e2e/avatar-fallback-verify.cjs
const { openApp, login, shot } = require('./helpers.cjs')

// 探针用「整段反引号模板」构建 evaluate 体——${sel} 在 Node 侧先插值，页面侧拿到的是字面选择器
const surfaceProbe = (sel) => `(() => {
  const el = document.querySelector('${sel}')
  if (!el) return { found: false }
  const cs = getComputedStyle(el)
  return { found: true, bgImage: cs.backgroundImage, radialCount: (cs.backgroundImage.match(/radial-gradient\\(/g)||[]).length, linearCount: (cs.backgroundImage.match(/linear-gradient\\(/g)||[]).length }
})()`

const letterProbe = (sel) => `(() => {
  const el = document.querySelector('${sel}')
  if (!el) return { found: false }
  const cs = getComputedStyle(el)
  return { found: true, color: cs.color, bgColor: cs.backgroundColor, radius: cs.borderRadius, borderTop: cs.borderTopWidth+' '+cs.borderTopStyle+' '+cs.borderTopColor, fontSize: cs.fontSize, zIndex: cs.zIndex }
})()`

;(async () => {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await login(page, { settleMs: 4500 })

  const totals = await page.evaluate(() => ({
    cardV2: document.querySelectorAll('.market-card-v2').length,
    fallbacks: document.querySelectorAll('.market-card-v2-cover-fallback').length,
    featuredFallbacks: document.querySelectorAll('.home-featured-fallback').length,
    letters: document.querySelectorAll('.market-card-v2-fallback-letter').length,
  }))
  const results = { home: totals, pageErrors: errors }

  if (totals.fallbacks > 0) {
    results.surface = await page.evaluate(surfaceProbe('.market-card-v2-cover-fallback'))
    results.letter = await page.evaluate(letterProbe('.market-card-v2-fallback-letter'))
    await shot(page, 'avatar-home-fallback.png')
  }

  // 圆头像共享表面：任意内联 avatarGradient（3 层）
  results.circleAvatars = await page.evaluate(`(() => {
    const els = Array.from(document.querySelectorAll('[style*="radial-gradient"]'))
    return {
      count: els.length,
      samples: els.slice(0, 2).map(el => ({
        cls: (el.className || '').toString().slice(0, 60),
        radialCount: ((el.style.background || '').match(/radial-gradient\\(/g)||[]).length,
        linearCount: ((el.style.background || '').match(/linear-gradient\\(/g)||[]).length,
      })),
    }
  })()`)

  // 合成兜底：已发布 CSS 类与数据无关地生效
  results.synthetic = await page.evaluate(`(() => {
    const wrap = document.createElement('div')
    wrap.style.cssText = 'width:180px;height:225px;position:fixed;left:-9999px'
    const s = document.createElement('div')
    s.className = 'market-card-v2-cover-fallback'
    s.style.background = 'radial-gradient(120% 90% at 18% 12%, rgba(255,255,255,0.34) 0%, rgba(255,255,255,0) 46%), radial-gradient(115% 115% at 85% 95%, rgba(0,0,0,0.22) 0%, rgba(0,0,0,0) 55%), linear-gradient(150deg, #6FAD74 0%, #8BC490 100%)'
    const l = document.createElement('span')
    l.className = 'market-card-v2-fallback-letter'
    l.textContent = '艾'
    s.appendChild(l); wrap.appendChild(s); document.body.appendChild(wrap)
    const cs = getComputedStyle(s); const lcs = getComputedStyle(l)
    const out = {
      bgImage: cs.backgroundImage,
      radialCount: (cs.backgroundImage.match(/radial-gradient\\(/g)||[]).length,
      linearCount: (cs.backgroundImage.match(/linear-gradient\\(/g)||[]).length,
      letterColor: lcs.color, letterBg: lcs.backgroundColor, letterRadius: lcs.borderRadius,
      letterBorder: lcs.borderTopWidth+' '+lcs.borderTopStyle+' '+lcs.borderTopColor, letterFont: lcs.fontSize,
    }
    wrap.remove(); return out
  })()`)

  console.log(JSON.stringify(results, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
