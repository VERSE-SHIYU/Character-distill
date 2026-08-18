const { openApp, login } = require('./helpers.cjs')
;(async () => {
  const { browser, page } = await openApp({ width: 390, height: 844 })
  let userId = null
  page.on('response', async (r) => {
    if (r.url().includes('/api/auth/login') && r.request().method() === 'POST') {
      try { const j = await r.json(); userId = j.user?.id || j.id } catch {}
    }
  })
  await login(page, { settleMs: 3000 })
  console.log('userId:', userId)
  const scan = await page.evaluate(async (uid) => {
    const tok = localStorage.getItem('auth_token')
    const res = await fetch('/api/market/author/' + uid, { headers: { Authorization: 'Bearer ' + tok } })
    const data = await res.json()
    const cards = data.cards || []
    const out = []
    for (const c of cards) {
      let cj = c.card_json
      if (typeof cj === 'string') { try { cj = JSON.parse(cj) } catch {} }
      const objFields = []
      const walk = (o, path) => {
        if (!o || typeof o !== 'object' || Array.isArray(o)) return
        const keys = Object.keys(o)
        if (keys.length === 2 && keys.includes('name') && keys.includes('description')) {
          objFields.push(path + ' → {name, description}')
        }
        for (const k of keys) {
          if (o[k] && typeof o[k] === 'object' && !Array.isArray(o[k])) walk(o[k], path + '.' + k)
        }
      }
      walk(cj, 'card_json')
      out.push({ id: c.id, name: c.name, objFields })
    }
    return { total: cards.length, out }
  }, userId)
  console.log(JSON.stringify(scan, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
