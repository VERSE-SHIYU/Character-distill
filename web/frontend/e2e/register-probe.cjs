// 注册 tab 三 bug 探针：测量溢出 + 输入框一致性 + 验证码按钮字体
// 用法: node e2e/register-probe.cjs
const { openApp } = require('./helpers.cjs')

async function probeRegister(page) {
  await page.goto('http://localhost:7861', { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForSelector('#login-username', { timeout: 15000 })
  // 切到注册 tab
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll('.login-tab')]
    const reg = btns.find((b) => b.textContent.trim() === '注册')
    if (reg) reg.click()
  })
  await page.waitForTimeout(400)

  return page.evaluate(() => {
    const r = (el) => {
      if (!el) return null
      const b = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return {
        cls: el.className?.toString().slice(0, 40),
        top: Math.round(b.top), bottom: Math.round(b.bottom),
        h: Math.round(b.height), w: Math.round(b.width),
        font: cs.fontSize, lineH: cs.lineHeight,
      }
    }
    const inputs = [...document.querySelectorAll('.login-field input, .login-pw-wrap input')].map((el) => {
      const b = el.getBoundingClientRect()
      return { label: (el.parentElement.querySelector('label')?.textContent || el.placeholder || 'code').trim(), h: Math.round(b.height), w: Math.round(b.width), font: getComputedStyle(el).fontSize }
    })
    return {
      viewport: { iw: window.innerWidth, ih: window.innerHeight },
      doc: {
        docElScrollH: document.documentElement.scrollHeight,
        docElClientH: document.documentElement.clientHeight,
        bodyScrollH: document.body.scrollHeight,
        bodyClientH: document.body.clientHeight,
        bodyPos: getComputedStyle(document.body).position,
        bodyOv: getComputedStyle(document.body).overflow,
      },
      page: r(document.querySelector('.login-page')),
      hero: r(document.querySelector('.login-hero')),
      brand: r(document.querySelector('.login-brand')),
      card: r(document.querySelector('.login-card')),
      cardScroll: (() => {
        const c = document.querySelector('.login-card')
        if (!c) return null
        return { clientH: c.clientHeight, scrollH: c.scrollHeight, scrollable: c.scrollHeight > c.clientHeight, ovY: getComputedStyle(c).overflowY }
      })(),
      inputs,
      codeBtn: r(document.querySelector('.login-code-btn')),
      submit: r(document.querySelector('.login-submit')),
    }
  })
}

// 扫描已部署 CSS 中 login-code 相关规则（判断 bundle 是否过期）
async function dumpLoginCss(page) {
  return page.evaluate(() => {
    const out = []
    for (const sheet of document.styleSheets) {
      let rules
      try { rules = sheet.cssRules } catch { continue }
      const walk = (list) => {
        for (const r of list) {
          if (r.cssRules) { walk(r.cssRules); continue }
          if (r.selectorText && r.selectorText.includes('login-')) {
            out.push(`${r.selectorText} { ${r.style.cssText} }`)
          }
        }
      }
      walk(rules)
    }
    return out
  })
}

;(async () => {
  for (const vp of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
    const { width: w, height: h } = vp
    const { browser, page, errors } = await openApp({ width: w, height: h })
    const data = await probeRegister(page)
    console.log(`\n═══ ${w}×${h} ═══\n` + JSON.stringify(data, null, 2))
    if (w === 390) {
      const css = await dumpLoginCss(page)
      const login = css.filter((s) => /code|login-field|login-page|login-card/.test(s))
      console.log('\n--- deployed login css (code/field/page/card) ---\n' + login.join('\n'))
    }
    await browser.close()
  }
})().catch((e) => { console.error(e); process.exit(1) })
