// @ts-check
// spec arc-phase-unlocated §6 前端触发链的自动验收。全程 page.route 模拟（无真实后端、无凭据）。
// 触发链：选阶段（全站 Select 下拉）→ 点「挪入」→ store.moveUnlocated POST /unlocated/move
//        → 写回接口返回的卡 → 弧线列表与未定位区都从新卡重算。
// 断言：
//   1) 未定位区列出三类条目；弧线阶段 overlay 按同形（嵌套）展示
//   2) 单值交换：阶段 1 的「语气」从「原值」变「新值」，「原值」回到未定位区；请求体对
//   3) 失败（400）：报错上屏，两边内容不变
// 用法：cd web/frontend && npx playwright test e2e/arc-phase-unlocated.spec.js
import { test, expect } from '@playwright/test'

const PHASES = (tone) => [
  { label: '穷酸要面子', state: 's1', overlay: { speaking_style: { tone } } },
  { label: '断腿之后', state: 's2', overlay: { speaking_style: { catchphrases: ['不要取笑'] } } },
]

function card(phase1Tone, looseTones) {
  return {
    name: '孔乙己',
    relationships: [{ target: '掌柜', relation: '债主', attitude: '', phase_attitudes: [] }],
    character_arc: {
      axis: '从争辩到不辩', selectable: true, source_fingerprint: 'FP',
      phases: PHASES(phase1Tone),
      unlocated: {
        behaviors: [{ situation: '被揭短', behavior: '涨红脸争辩' }],
        overlay: { speaking_style: { tone: looseTones } },
        attitudes: [{ target: '掌柜', attitude: '怕他记账', note: '', phase: 1 }],
      },
    },
  }
}

async function setup(page, moveReply) {
  const sent = []
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const url = route.request().url()
    if (url.includes('/api/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ id: 'u1', username: 't', has_api_key: true, role: 'user' }) })
    }
    if (url.includes('/api/distill/cards/')) {           // 列表接口真实返回数组（store 会 .map）
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    }
    if (url.includes('/unlocated/move')) {
      sent.push(JSON.parse(route.request().postData() || '{}'))
      return route.fulfill(moveReply)
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.addInitScript(() => {
    localStorage.setItem('auth_token', 'x')
    localStorage.setItem('nav_view', 'character')
  })
  await page.goto('/')
  // 等 App 挂载时的 /api/auth/me 流程写完 authUser 再注入卡：先注入会被它随后的状态恢复竞争掉
  await page.waitForFunction(() => window.__appStore?.getState().authUser, { timeout: 8000 })
  await page.evaluate((json) => {
    window.__appStore.setState({
      isLoggedIn: true, currentView: 'character', currentTextId: 't1',
      texts: [{ id: 't1', filename: 'mock.txt' }], cards: [],
      currentCard: { id: 'c1', name: '孔乙己', text_id: 't1', card_json: json },
    })
  }, JSON.stringify(card('原值', ['新值'])))
  await expect(page.locator('.card-unlocated')).toBeVisible({ timeout: 8000 })
  return sent
}

const toneRow = (page) => page.locator('.card-unlocated-item').filter({ hasText: '语气' })

test('未定位区列出三类条目；阶段 overlay 按嵌套展示', async ({ page }) => {
  await setup(page, { status: 200, body: '{}' })
  await expect(page.locator('.card-unlocated-item')).toHaveCount(3)
  await expect(page.locator('.card-arc-overlay').nth(0)).toContainText('语气')
  await expect(page.locator('.card-arc-overlay').nth(0)).toContainText('原值')
  await expect(page.locator('.card-arc-overlay').nth(1)).toContainText('口头禅')
  await page.screenshot({ path: 'e2e/arc-phase-unlocated-before.png', fullPage: true })
})

test('单值交换：挪入阶段 1 后两边都变，请求体正确', async ({ page }) => {
  const body = JSON.stringify({ ok: true, card: { id: 'c1', card_json: JSON.stringify(card('新值', ['原值'])) } })
  const sent = await setup(page, { status: 200, contentType: 'application/json', body })
  await toneRow(page).getByRole('combobox').click()
  await page.getByRole('option', { name: '阶段 1 · 穷酸要面子' }).click()
  await toneRow(page).getByRole('button', { name: '挪入' }).click()
  await expect(page.locator('.card-arc-overlay').nth(0)).toContainText('新值')
  await expect(toneRow(page)).toContainText('原值')
  expect(sent).toEqual([{ section: 'overlay', index: 0, phase: 1, path: 'speaking_style.tone' }])
  await page.screenshot({ path: 'e2e/arc-phase-unlocated-after.png', fullPage: true })
})

test('挪动失败：报错上屏，两边不变', async ({ page }) => {
  await setup(page, { status: 400, contentType: 'application/json',
    body: JSON.stringify({ detail: '这一条已经不在未定位区，请刷新后重试' }) })
  await toneRow(page).getByRole('button', { name: '挪入' }).click()
  await expect(page.getByText('请刷新后重试')).toBeVisible()
  await expect(page.locator('.card-arc-overlay').nth(0)).toContainText('原值')
  await expect(toneRow(page)).toContainText('新值')
})
