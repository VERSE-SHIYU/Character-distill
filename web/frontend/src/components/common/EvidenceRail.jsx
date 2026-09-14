import { useState } from 'react'
import { Book, ChevronDown, Globe, Sparkles } from './Icon'

// 消费的是 core/schema.py 的落库快照形状（SSE evidence 帧 / 历史接口 / 重逢接口三处同形）。
// kind → 显示名的唯一处；新增 kind 若没登记在这里 = 未知 kind，静默不渲染（不崩）。
const KIND = {
  scene: { label: '剧情原文', noun: '段原文', Icon: Book },
  memory: { label: '记忆', noun: '条记忆', Icon: Sparkles },
  web: { label: '网络', noun: '条网络结果', Icon: Globe },
}

// 四态里除 hit 外的三种各自有专属文案 —— 三者显示成同一个空白，
// 等于把后端的四态设计在最后一米作废。
const STATUS_TEXT = {
  empty: '没查到相关内容',
  failed: '检索失败',
  timeout: '检索超时',
}

// 未命中的各报各的（同一种状态只报一次）。
function nonHitStatuses(traces) {
  const out = []
  for (const t of traces) {
    if (t.status !== 'hit' && STATUS_TEXT[t.status] && !out.includes(STATUS_TEXT[t.status])) {
      out.push(STATUS_TEXT[t.status])
    }
  }
  return out
}

// 折叠态摘要：命中按 kind 计数，未命中的按状态各报各的。混合时并列，
// 例如「1 段原文 · 检索超时」—— 有命中也不能把另一路的超时吞掉。
function summaryParts(traces) {
  const counts = new Map()
  for (const t of traces) {
    if (t.status === 'hit') {
      counts.set(t.source, (counts.get(t.source) || 0) + (t.items?.length || 0))
    }
  }
  const parts = []
  for (const kind of Object.keys(KIND)) {
    if (counts.has(kind)) parts.push(`${counts.get(kind)} ${KIND[kind].noun}`)
  }
  return [...parts, ...nonHitStatuses(traces)]
}

function EvidenceCard({ trace }) {
  const { label, Icon } = KIND[trace.source]
  return (
    <div className="evidence-card">
      <div className="evidence-card-head">
        <Icon size={12} />
        <span>{label}</span>
        {/* 未命中时展开态说清是哪一路、以及为什么没内容（空/失败/超时） */}
        {trace.status !== 'hit' && STATUS_TEXT[trace.status] && (
          <span className="evidence-card-status">{STATUS_TEXT[trace.status]}</span>
        )}
      </div>
      {(trace.items || []).map((it, i) => (
        <div className="evidence-item" key={i}>
          <span className="evidence-item-text">{it.text}</span>
          {it.truncated && <span className="evidence-item-truncated">（已截断）</span>}
          {/* meta.url 是契约里声明的回查句柄（WebMeta）。取不到就是 None —— 契约明令
              不得渲染成空链接或死链，所以有才渲染。这不是反解 meta 内部结构。 */}
          {it.meta?.url && (
            <a className="evidence-item-link" href={it.meta.url} target="_blank" rel="noreferrer noopener">
              {it.meta.source || it.meta.url}
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

/**
 * 检索来源条。**默认折叠**，只留一行「检索来源：…」。
 *
 * 为什么默认折叠：这是角色扮演，答案该是戏。满屏证据卡会把对话页变成检索日志，
 * 沉浸感没了 —— 用户需要的是「想看时看得到」（可核验），不是「每句都被来源压着」。
 * 所以折叠态给结论（查到了几段、哪路没查到），展开态才给原文。
 *
 * 老消息、非 agent 模式、群聊、legacy 路径都不会带 evidence（或带 null），
 * 此时整个组件不渲染一个节点 —— 不占位、不抛错。
 */
export default function EvidenceRail({ evidence }) {
  const [open, setOpen] = useState(false)
  if (!evidence?.length) return null
  const traces = evidence.filter((t) => KIND[t.source])
  const parts = summaryParts(traces)
  if (!parts.length) return null
  return (
    <div className="evidence-rail">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ChevronDown size={12} className={`evidence-arrow${open ? ' evidence-arrow-open' : ''}`} />
        <span className="evidence-summary">检索来源：{parts.join(' · ')}</span>
      </button>
      {open && (
        <div className="evidence-body">
          {traces.map((t, i) => (
            <EvidenceCard key={`${t.source}-${i}`} trace={t} />
          ))}
        </div>
      )}
    </div>
  )
}
