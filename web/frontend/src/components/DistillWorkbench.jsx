import { useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import useSmoothProgress from '../hooks/useSmoothProgress'
import useIsMobile from '../hooks/useIsMobile'
import { parseCardJson } from '../utils/card'
import Avatar from './common/Avatar'
import { AlertTriangle, Check, Clock, CornerUpLeft, Download, RefreshCw, Sparkles } from './common/Icon'

// 5 步视觉状态机：由后端 status 诚实映射（queued→采集语料 … formatting/saving→验收上线）
const STEPS = ['采集语料', '提取人格', '生成人设', '校准语气', '验收上线']
const STATUS_STEP = { queued: 1, identifying: 2, analyzing: 3, merging: 4, formatting: 5, saving: 5 }

const STATUS_TEXT = {
  queued: '排队中',
  identifying: '识别角色',
  analyzing: '生成人设',
  merging: '校准语气',
  formatting: '验收上线',
  saving: '保存卡片',
  done: '已完成',
  error: '失败',
}

const LOG_TEXT = {
  queued: '任务已排队，等待开始',
  identifying: '正在识别语料中的角色…',
  analyzing: '正在分析语料，生成人设…',
  merging: '正在合并素材，校准语气…',
  formatting: '正在格式化人设，验收中…',
  saving: '正在保存角色卡…',
  done: '蒸馏完成，角色卡已生成',
}

const pad = (n) => String(n).padStart(2, '0')
const nowHM = () => {
  const d = new Date()
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function exportCard(card) {
  const name = (typeof card.card_json === 'string' ? parseCardJson(card).name : card.name) || '角色卡'
  try {
    const res = await fetchWithTimeout(`/api/distill/cards/${card.id}/export?format=raw`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${name}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    console.warn('[distill-workbench] export failed:', e)
  }
}

function shortId(id) {
  return id && id.length > 10 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id || '—'
}

export default function DistillWorkbench() {
  const tasks = useAppStore((s) => s.distillTasks)
  const currentTextId = useAppStore((s) => s.currentTextId)
  const texts = useAppStore((s) => s.texts)
  const loadTexts = useAppStore((s) => s.loadTexts)
  const isMobile = useIsMobile()

  const [cards, setCards] = useState([])
  const [selKey, setSelKey] = useState(null)

  // 已验收区：逐文本 + 独立卡片（照 CharacterManagement 的数据源，card_json 是字符串）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (texts.length === 0) { loadTexts(); return }
      const all = []
      for (const t of texts) {
        try {
          const res = await fetchWithTimeout(`/api/distill/cards/by-text/${t.id}`)
          if (res.ok) {
            for (const c of await res.json()) all.push(c)
          }
        } catch {}
      }
      try {
        const res = await fetchWithTimeout('/api/distill/cards/standalone')
        if (res.ok) {
          for (const c of await res.json()) all.push(c)
        }
      } catch {}
      if (!cancelled) setCards(all)
    })()
    return () => { cancelled = true }
  }, [texts, loadTexts])

  const taskItems = tasks
  const cardItems = cards

  const firstKey = taskItems[0] ? `t:${taskItems[0].id}` : cardItems[0] ? `c:${cardItems[0].id}` : null

  // 桌面默认选中第一个任务（无任务则第一张卡）；移动端先显示列表
  useEffect(() => {
    if (!isMobile && selKey === null && firstKey) setSelKey(firstKey)
  }, [selKey, firstKey, isMobile])

  // 选中项被移除时：桌面回退到第一个，移动端回列表
  useEffect(() => {
    if (!selKey) return
    const [t, id] = selKey.split(':')
    const exists = t === 't'
      ? taskItems.some((x) => x.id === id)
      : cardItems.some((x) => x.id === id)
    if (!exists) setSelKey(isMobile ? null : firstKey)
  }, [selKey, taskItems.length, cardItems.length, firstKey, isMobile]) // eslint-disable-line react-hooks/exhaustive-deps

  const sel = useMemo(() => {
    if (!selKey) return null
    const [t, id] = selKey.split(':')
    if (t === 't') {
      const item = taskItems.find((x) => x.id === id)
      return item ? { type: 'task', item } : null
    }
    const item = cardItems.find((x) => x.id === id)
    return item ? { type: 'card', item } : null
  }, [selKey, taskItems, cardItems])

  if (isMobile) {
    return (
      <div className="dw-shell dw-mobile">
        {sel ? (
          <DetailPane sel={sel} onBack={() => setSelKey(null)} />
        ) : (
          <ListPane tasks={taskItems} cards={cardItems} currentTextId={currentTextId} selKey={selKey} onSelect={setSelKey} onPop={null} />
        )}
      </div>
    )
  }

  return (
    <div className="dw-shell dw-desktop">
      <ListPane tasks={taskItems} cards={cardItems} currentTextId={currentTextId} selKey={selKey} onSelect={setSelKey} onPop={null} />
      <div className="dw-detail-panel">
        {sel ? <DetailPane sel={sel} /> : <EmptyDetail />}
      </div>
    </div>
  )
}

function ListPane({ tasks, cards, currentTextId, selKey, onSelect, onPop }) {
  const pushView = useAppStore((s) => s.pushView)
  const counts = `${tasks.length} 进行中 · ${cards.length} 已验收`
  const empty = tasks.length === 0 && cards.length === 0

  return (
    <div className="dw-task-panel">
      <div className="dw-task-head">
        <div className="dw-task-title-row">
          <h3 className="dw-task-title">蒸馏任务</h3>
          {onPop && (
            <button className="dw-back-btn" onClick={onPop}><CornerUpLeft size={16} /></button>
          )}
        </div>
        <p className="dw-task-sub">{counts}</p>
      </div>

      <div className="dw-task-list">
        {tasks.length > 0 && (
          <div className="dw-section-label">进行中</div>
        )}
        {tasks.map((t) => (
          <TaskRow key={t.id} task={t} active={selKey === `t:${t.id}`} onClick={() => onSelect(`t:${t.id}`)} />
        ))}

        {cards.length > 0 && (
          <div className="dw-section-label">已验收</div>
        )}
        {cards.map((c) => {
          const data = typeof c.card_json === 'string' ? parseCardJson(c) : c.card_json || {}
          const name = data.name || c.name || '未命名'
          const created = c.created_at ? String(c.created_at).slice(0, 16) : ''
          return (
            <button
              key={c.id}
              type="button"
              className={`dw-task-item${selKey === `c:${c.id}` ? ' is-active' : ''}`}
              onClick={() => onSelect(`c:${c.id}`)}
            >
              <Avatar name={name} size={34} />
              <span className="dw-item-body">
                <span className="dw-item-name">{name}</span>
                <span className="dw-item-sub">{created}</span>
              </span>
              <span className="dw-item-badge is-done">已验收</span>
            </button>
          )
        })}

        {empty && (
          <div className="dw-empty">
            <Sparkles size={28} />
            <p>还没有蒸馏任务</p>
            <button type="button" className="dw-empty-btn" onClick={() => pushView('character')}>
              去角色页开始
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function TaskRow({ task, active, onClick }) {
  const displayPct = useSmoothProgress(task.progress_pct, task.status === 'done')
  const isDone = task.status === 'done'
  const isError = task.status === 'error'
  const running = !isDone && !isError
  const statusText = isDone ? '已完成' : isError ? '失败' : `${STATUS_TEXT[task.status] || '蒸馏中'} · ${Math.round(displayPct)}%`

  return (
    <button
      type="button"
      className={`dw-task-item${active ? ' is-active' : ''}${isError ? ' is-error' : ''}`}
      onClick={onClick}
    >
      <Avatar name={task.character || '?'} size={34} />
      <span className="dw-item-body">
        <span className="dw-item-name">{task.character || '未命名角色'}</span>
        <span className="dw-item-sub">{statusText}</span>
      </span>
      {running ? (
        <span className="dw-item-progress" style={{ width: 52 }}>
          <span className="dw-item-progress-fill" style={{ width: `${Math.max(displayPct, 3)}%` }} />
        </span>
      ) : (
        <span className={`dw-item-badge${isDone ? ' is-done' : ' is-error'}`}>{isDone ? '已完成' : '失败'}</span>
      )}
    </button>
  )
}

function EmptyDetail() {
  return (
    <div className="dw-detail-empty">
      <Sparkles size={30} />
      <p>从左侧选择任务查看进度</p>
    </div>
  )
}

function DetailPane({ sel, onBack }) {
  if (sel.type === 'task') return <TaskDetail task={sel.item} onBack={onBack} />
  return <CardDetail card={sel.item} onBack={onBack} />
}

function TaskDetail({ task, onBack }) {
  const removeDistillTask = useAppStore((s) => s.removeDistillTask)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const pushView = useAppStore((s) => s.pushView)
  const displayPct = useSmoothProgress(task.progress_pct, task.status === 'done')

  const isDone = task.status === 'done'
  const isError = task.status === 'error'
  const running = !isDone && !isError
  const activeStep = isDone ? 5 : STATUS_STEP[task.status] || 1
  const statusText = isDone ? '已完成' : isError ? '失败' : `${STATUS_TEXT[task.status] || '蒸馏中'} · ${Math.round(displayPct)}%`

  // 本地状态机日志：监听 status 变化追加
  const [logs, setLogs] = useState([])
  const prevStatus = useRef(null)
  useEffect(() => {
    if (!task) { prevStatus.current = null; return }
    const st = task.status
    if (st === prevStatus.current) return
    const isFirst = prevStatus.current === null
    prevStatus.current = st
    const text = isFirst ? '任务已创建' : (LOG_TEXT[st] || (isError ? task.message || '蒸馏失败' : `进入阶段：${STATUS_TEXT[st] || st}`))
    const state = st === 'done' ? 'done' : st === 'error' ? 'error' : 'run'
    setLogs((l) => [...l.slice(-6), { t: nowHM(), text, state }])
  }, [task, task?.status, isError]) // eslint-disable-line react-hooks/exhaustive-deps

  const cancelTask = () => {
    if (running) fetchWithTimeout(`/api/distill/task/${task.id}`, { method: 'DELETE' }).catch(() => {})
    removeDistillTask(task.id)
  }
  const retryTask = () => {
    removeDistillTask(task.id)
    distillCharacter(task.textId, task.character)
  }
  const tryChat = async () => {
    const st = useAppStore.getState()
    if (task.textId) await st.loadCards(task.textId)
    const card = useAppStore.getState().cards.find((c) => c.id === task.card_id)
    if (card) st.startChat(card)
  }

  return (
    <div className="dw-detail-scroll">
      {onBack && (
        <button className="dw-back-btn dw-back-btn-mobile" onClick={onBack}><CornerUpLeft size={16} /> 返回</button>
      )}

      <div className="dw-hero">
        <span className="dw-stage-glow" />
        <div className="dw-hero-row">
          <div className="dw-hero-id">
            <h2 className="dw-hero-title">{task.character || '蒸馏任务'}</h2>
            <p className="dw-hero-sub">任务 {shortId(task.id)} · 文本 {shortId(task.textId)}</p>
          </div>
          <span className={`dw-badge${isDone ? ' is-done' : isError ? ' is-error' : ''}`}>{statusText}</span>
        </div>
      </div>

      <div className="dw-stepper">
        {STEPS.map((label, i) => {
          const n = i + 1
          const cls = isDone ? ' is-done' : isError && n === activeStep ? ' is-error' : n < activeStep ? ' is-done' : n === activeStep ? ' is-active' : ''
          return (
            <div key={label} className={`dw-step${cls}`}>
              <span className="dw-step-dot">{n < activeStep || isDone ? <Check size={13} /> : n}</span>
              <span className="dw-step-label">{label}</span>
            </div>
          )
        })}
      </div>

      {isError && (
        <div className="dw-error-banner"><AlertTriangle size={14} />{task.message || '蒸馏失败'}</div>
      )}

      <div className="dw-progress"><span className="dw-progress-bar" style={{ width: `${displayPct}%` }} /></div>

      <div className="dw-stat-grid">
        <div className="dw-tstat"><b>{Math.round(displayPct)}%</b><span>总体进度</span></div>
        <div className="dw-tstat"><b>{task.current && task.total ? `${task.current}/${task.total}` : '—'}</b><span>语料段落</span></div>
        <div className="dw-tstat"><b>{STATUS_TEXT[task.status] || '—'}</b><span>任务状态</span></div>
        <div className="dw-tstat"><b>{task.character || '—'}</b><span>蒸馏角色</span></div>
      </div>

      <div className="dw-log-card">
        <div className="dw-log-eyebrow"><Clock size={13} /> 实时日志</div>
        {logs.map((l, i) => (
          <div key={i} className="dw-log-line">
            <span className={`dw-dot dw-dot-${l.state}`} />
            <span className="dw-log-t">{l.t}</span>
            <span className="dw-log-text">{l.text}</span>
          </div>
        ))}
        {logs.length === 0 && <div className="dw-log-line"><span className="dw-dot dw-dot-wait" /><span className="dw-log-text">等待任务状态…</span></div>}
      </div>

      <div className="dw-cta">
        {running && <button type="button" className="dw-cta-btn dw-cta-secondary" onClick={cancelTask}>暂停任务</button>}
        {isError && task.textId && <button type="button" className="dw-cta-btn dw-cta-primary" onClick={retryTask}><RefreshCw size={15} /> 重新蒸馏</button>}
        {isDone && (
          <>
            <button type="button" className="dw-cta-btn dw-cta-primary" onClick={tryChat}><Sparkles size={15} /> 试聊当前版本</button>
            <button type="button" className="dw-cta-btn dw-cta-secondary" onClick={() => exportCard({ id: task.card_id, card_json: null, name: task.character })}><Download size={15} /> 导出卡片</button>
          </>
        )}
        <button type="button" className="dw-cta-btn dw-cta-ghost" onClick={() => pushView('character')}>返回角色页</button>
      </div>
    </div>
  )
}

function CardDetail({ card, onBack }) {
  const startChat = useAppStore((s) => s.startChat)
  const viewCard = useAppStore((s) => s.viewCard)
  const pushView = useAppStore((s) => s.pushView)
  const distillCharacter = useAppStore((s) => s.distillCharacter)

  const data = typeof card.card_json === 'string' ? parseCardJson(card) : card.card_json || {}
  const name = data.name || card.name || '未命名'
  const created = card.created_at ? String(card.created_at).slice(0, 16) : ''
  const tags = Array.isArray(data.tags) ? data.tags : []

  const editCard = () => {
    viewCard(card)
    pushView('character')
  }
  const redistill = () => {
    if (card.text_id) distillCharacter(card.text_id, name, true)
  }

  return (
    <div className="dw-detail-scroll">
      {onBack && (
        <button className="dw-back-btn dw-back-btn-mobile" onClick={onBack}><CornerUpLeft size={16} /> 返回</button>
      )}

      <div className="dw-hero">
        <span className="dw-stage-glow" />
        <div className="dw-hero-row">
          <div className="dw-hero-id">
            <h2 className="dw-hero-title">{name}</h2>
            <p className="dw-hero-sub">{created}{card.text_id ? ` · 文本 ${shortId(card.text_id)}` : ' · 独立卡片'}</p>
          </div>
          <span className="dw-badge is-done">已验收</span>
        </div>
      </div>

      {data.identity && <p className="dw-card-identity">{data.identity}</p>}
      {tags.length > 0 && (
        <div className="dw-card-tags">
          {tags.map((t) => <span key={t} className="dw-card-tag">{t}</span>)}
        </div>
      )}
      {data.awakening_message && (
        <div className="dw-awakening">
          <span className="dw-awakening-label">苏醒台词</span>
          <p className="dw-awakening-text">「{data.awakening_message}」</p>
        </div>
      )}

      <div className="dw-cta">
        <button type="button" className="dw-cta-btn dw-cta-primary" onClick={() => startChat(card)}><Sparkles size={15} /> 试聊</button>
        <button type="button" className="dw-cta-btn dw-cta-secondary" onClick={() => exportCard(card)}><Download size={15} /> 导出</button>
        <button type="button" className="dw-cta-btn dw-cta-secondary" onClick={editCard}>编辑</button>
        {card.text_id && <button type="button" className="dw-cta-btn dw-cta-ghost" onClick={redistill}><RefreshCw size={15} /> 重新蒸馏</button>}
      </div>
    </div>
  )
}
