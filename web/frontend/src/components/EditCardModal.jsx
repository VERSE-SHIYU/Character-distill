import { useEffect, useRef, useState } from 'react'
import ErrorBox from './common/ErrorBox'
import ObjectListField from './common/ObjectListField'
import { withRowKeys, cleanRows } from '../utils/objectRows'
import { examplesByPhase, examplesToText, textToExamples } from '../utils/dialogueExamples'

function splitLines(val) {
  return (Array.isArray(val) ? val.join('\n') : val || '')
}

function joinLines(val) {
  return val.split('\n').map((s) => s.trim()).filter(Boolean)
}

const LIMITS = {
  identity: { max: 80 },
  personality_traits: { max: 600, maxLines: 8, perLine: 100 },
  tone: { max: 90 },
  sentence_pattern: { max: 100 },
  catchphrases: { max: 240, maxLines: 6, perLine: 40 },
  vocabulary_level: { max: 40 },
  taboo_words: { max: 120, maxLines: 6, perLine: 20 },
  values: { max: 180, maxLines: 6, perLine: 30 },
  key_memories: { max: 800, maxLines: 8, perLine: 80 },
  inner_tensions: { max: 500, maxLines: 5, perLine: 100 },
  background: { max: 300 },
  first_message: { max: 300 },
  emotional_patterns: { max: 550, maxLines: 6, perLine: 70 },
  decision_style: { max: 180 },
  arc_axis: { max: 40 },
  dialogue_examples: { max: 600 },
}

// 三张对象列表的列定义与条数上限。上限只管「还能不能再加」（见 ObjectListField）。
const REL_MAX = 8
const REL_COLUMNS = [
  { key: 'target', placeholder: '对方名字', maxLength: 20 },
  { key: 'relation', placeholder: '关系', maxLength: 20 },
  { key: 'attitude', placeholder: '态度', maxLength: 50 },
]
const ARC_MAX = 5
const ARC_COLUMNS = [
  { key: 'label', placeholder: '心态/立场', maxLength: 8 },
  { key: 'state', placeholder: '这一阶段的状态（句首点明故事时期）', maxLength: 100, flex: 3 },
]
const BEHAVIOR_MAX = 12        // 从头到尾都成立的做法
const PHASE_BEHAVIOR_MAX = 6   // 每个阶段专属的做法
const BEHAVIOR_COLUMNS = [
  { key: 'situation', placeholder: '遇到什么情境', maxLength: 40 },
  { key: 'behavior', placeholder: '具体怎么做', maxLength: 80, flex: 2 },
]

// 表单的初值：只在表单挂载时从卡上取一次（见 EditCardModal）。
function initialForm(data) {
  const style = data.speaking_style || {}
  return {
    name: data.name || '',
    identity: data.identity || '',
    personality_traits: splitLines(data.personality_traits),
    tone: style.tone || '',
    sentence_pattern: style.sentence_pattern || '',
    catchphrases: splitLines(style.catchphrases),
    vocabulary_level: style.vocabulary_level || '',
    taboo_words: splitLines(style.taboo_words),
    values: splitLines(data.values),
    key_memories: splitLines(data.key_memories),
    inner_tensions: splitLines(data.inner_tensions),
    background: data.background || '',
    first_message: data.first_message || '',
    emotional_patterns: splitLines(data.emotional_patterns),
    decision_style: data.decision_style || '',
    arc_axis: data.character_arc?.axis || '',
    dialogue_examples: examplesToText(data.dialogue_examples),
  }
}

// 弹窗每次打开都重新挂载：表单的初值只在挂载时从 `data` 取一次。所以开着的这一次，用户填的
// 内容不会被父组件重渲染冲掉；关掉再开（或换了一张卡）拿到的一定是现在这张卡。宿主是一直挂着
// 本组件还是打开时才挂，结果都一样。
export default function EditCardModal({ isOpen, ...props }) {
  if (!isOpen) return null
  return <EditCardSession {...props} />
}

// 一次打开。「重新找一次」成功后卡变了：换一份新表单（`round` 变 → 重新挂载），按找过之后的
// 这张卡、这一版重新取初值；找的结果（`refound`）留在这一层，新表单接着显示。
function EditCardSession({ onRefindExamples, ...props }) {
  const [round, setRound] = useState(0)
  const [refound, setRefound] = useState(null)   // null 还没找过 / true 找到了 / false 没找到
  return (
    <EditCardForm
      key={round}
      {...props}
      refound={refound}
      onRefindExamples={refound === null ? onRefindExamples : undefined}
      onRefound={(found) => { setRefound(Boolean(found)); setRound((n) => n + 1) }}
    />
  )
}

function EditCardForm({
  data: liveData, cardId, revision, onSave, onClose, editName = false,
  onRefindExamples, refound, onRefound,
}) {
  // 表单编辑的是挂载那一刻的这张卡、这一版（`opened`）：之后宿主那边的卡再怎么变，本表单读的、
  // 保存时交回去的都是它。按这一版提交，卡在别处变过由后端的版本锁报冲突，不在这里合并。
  const [opened] = useState(() => ({ data: liveData, cardId, revision }))
  const data = opened.data
  const style = data.speaking_style || {}

  const [form, setForm] = useState(() => initialForm(data))
  const [saving, setSaving] = useState(false)
  const [refinding, setRefinding] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [touched, setTouched] = useState(false)
  const [relationships, setRelationships] = useState(() => withRowKeys(data.relationships))
  const [arcPhases, setArcPhases] = useState(() =>
    withRowKeys(data.character_arc?.phases).map((p) => ({ ...p, behaviors: withRowKeys(p.behaviors) })))
  const [behaviors, setBehaviors] = useState(() => withRowKeys(data.situation_behaviors))

  // 用户的每一处修改都经这里：记下「改过」，「重新找一次」据此先问一句再换表单。
  const edit = (setter) => (value) => { setTouched(true); setter(value) }
  const update = (field, value) => edit(setForm)((f) => ({ ...f, [field]: value }))

  // 保存或重新找没结束时不让关：关了，结果（尤其是失败提示）就没处显示了。
  const busy = saving || refinding
  const close = () => { if (!busy) onClose() }

  // 为补示例而开（待补，或刚找过）：把「对话示例」那一栏滚到眼前 —— 它在一张长表单的底部。
  // 要不要滚在挂载时定下，只滚这一次。
  const examplesRef = useRef(null)
  const [showExamplesFirst] = useState(() => Boolean(onRefindExamples) || refound !== null)
  useEffect(() => {
    if (showExamplesFirst) examplesRef.current?.scrollIntoView?.({ block: 'center' })
  }, [showExamplesFirst])

  const handleRefind = async () => {
    if (touched && !window.confirm('重新找会放弃你刚才没保存的修改，继续吗？')) return
    setRefinding(true)
    setSaveError(null)
    try {
      onRefound(await onRefindExamples({ cardId: opened.cardId, revision: opened.revision }))
    } catch (err) {
      // 没找成（调用出错）：机会还在，表单和按钮都留着
      setSaveError(err.message || '重新找失败，请稍后再试')
      setRefinding(false)
    }
  }

  const FIELD_LABELS = {
    personality_traits: '性格特征',
    catchphrases: '口癖',
    taboo_words: '禁忌用词',
    values: '核心价值观',
    key_memories: '关键记忆',
    inner_tensions: '内在矛盾',
    emotional_patterns: '情感模式',
  }

  const handleSave = async () => {
    const checks = [
      { field: 'personality_traits', lines: form.personality_traits.split('\n').filter(Boolean) },
      { field: 'catchphrases', lines: form.catchphrases.split('\n').filter(Boolean) },
      { field: 'taboo_words', lines: form.taboo_words.split('\n').filter(Boolean) },
      { field: 'values', lines: form.values.split('\n').filter(Boolean) },
      { field: 'key_memories', lines: form.key_memories.split('\n').filter(Boolean) },
      { field: 'inner_tensions', lines: form.inner_tensions.split('\n').filter(Boolean) },
      { field: 'emotional_patterns', lines: form.emotional_patterns.split('\n').filter(Boolean) },
    ]
    for (const { field, lines } of checks) {
      const limit = LIMITS[field]
      if (limit.maxLines && lines.length > limit.maxLines) {
        alert(`「${FIELD_LABELS[field] || field}」最多 ${limit.maxLines} 行，当前 ${lines.length} 行`)
        return
      }
      if (limit.perLine) {
        for (const line of lines) {
          if (line.length > limit.perLine) {
            alert(`「${FIELD_LABELS[field] || field}」每行最多 ${limit.perLine} 个字符`)
            return
          }
        }
      }
    }
    if (relationships.length > REL_MAX) {
      alert(`人物关系最多 ${REL_MAX} 条`)
      return
    }
    const cardJson = {
      ...data,
      name: editName ? form.name.trim() : data.name,
      identity: form.identity,
      personality_traits: joinLines(form.personality_traits),
      speaking_style: {
        ...style,
        tone: form.tone,
        sentence_pattern: form.sentence_pattern,
        catchphrases: joinLines(form.catchphrases),
        vocabulary_level: form.vocabulary_level,
        taboo_words: joinLines(form.taboo_words),
      },
      values: joinLines(form.values),
      key_memories: joinLines(form.key_memories),
      inner_tensions: joinLines(form.inner_tensions),
      background: form.background,
      first_message: form.first_message,
      emotional_patterns: joinLines(form.emotional_patterns),
      decision_style: form.decision_style,
      // 只替换表单编辑的两项；起点指纹、未定位区等原样带回（整体替换会丢指纹 → 卡失去选阶段能力）
      character_arc: {
        ...data.character_arc,
        axis: form.arc_axis.trim(),
        phases: cleanRows(arcPhases, ARC_COLUMNS)
          .map((p) => ({ ...p, behaviors: cleanRows(p.behaviors || [], BEHAVIOR_COLUMNS) })),
      },
      situation_behaviors: cleanRows(behaviors, BEHAVIOR_COLUMNS),
      relationships: cleanRows(relationships, REL_COLUMNS),
      dialogue_examples: textToExamples(form.dialogue_examples),
    }
    setSaving(true)
    setSaveError(null)
    try {
      await onSave(cardJson, { cardId: opened.cardId, revision: opened.revision })
    } catch (err) {
      // 弹窗不关：用户改写的内容不能因为一次失败就丢
      setSaveError(err.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal-card edit-card-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">编辑角色卡 — {data.name}</div>

        <ExamplesNotice
          onRefind={onRefindExamples ? handleRefind : undefined}
          refinding={refinding}
          disabled={busy}
          refound={refound}
        />

        {saveError && <ErrorBox message={saveError} onDismiss={() => setSaveError(null)} />}

        <div className="edit-form-scroll">
          {editName && (
            <Field label="名称">
              <input className="modal-input" value={form.name} onChange={(e) => update('name', e.target.value)} maxLength={40} />
            </Field>
          )}
          <Field label="一句话身份" mono field="identity" value={form.identity}>
            <input className="modal-input" value={form.identity} onChange={(e) => update('identity', e.target.value)} maxLength={LIMITS.identity.max} />
          </Field>

          <Field label="性格特征（每行一条）" mono field="personality_traits" value={form.personality_traits}>
            <textarea className="modal-textarea" rows={3} value={form.personality_traits} onChange={(e) => update('personality_traits', e.target.value)} maxLength={LIMITS.personality_traits.max} />
          </Field>

          <Field label="语气" mono field="tone" value={form.tone}>
            <input className="modal-input" value={form.tone} onChange={(e) => update('tone', e.target.value)} maxLength={LIMITS.tone.max} />
          </Field>

          <Field label="句式特征" mono field="sentence_pattern" value={form.sentence_pattern}>
            <input className="modal-input" value={form.sentence_pattern} onChange={(e) => update('sentence_pattern', e.target.value)} maxLength={LIMITS.sentence_pattern.max} />
          </Field>

          <Field label="口癖（每行一条）" mono field="catchphrases" value={form.catchphrases}>
            <textarea className="modal-textarea" rows={2} value={form.catchphrases} onChange={(e) => update('catchphrases', e.target.value)} maxLength={LIMITS.catchphrases.max} />
          </Field>

          <Field label="用词水平" mono field="vocabulary_level" value={form.vocabulary_level}>
            <input className="modal-input" value={form.vocabulary_level} onChange={(e) => update('vocabulary_level', e.target.value)} maxLength={LIMITS.vocabulary_level.max} />
          </Field>

          <Field label="禁忌用词（每行一条）" mono field="taboo_words" value={form.taboo_words}>
            <textarea className="modal-textarea" rows={2} value={form.taboo_words} onChange={(e) => update('taboo_words', e.target.value)} maxLength={LIMITS.taboo_words.max} />
          </Field>

          <Field label="核心价值观（每行一条）" mono field="values" value={form.values}>
            <textarea className="modal-textarea" rows={2} value={form.values} onChange={(e) => update('values', e.target.value)} maxLength={LIMITS.values.max} />
          </Field>

          <Field label="关键记忆（每行一条）" mono field="key_memories" value={form.key_memories}>
            <textarea className="modal-textarea" rows={3} value={form.key_memories} onChange={(e) => update('key_memories', e.target.value)} maxLength={LIMITS.key_memories.max} />
          </Field>

          <Field label="内在矛盾（每行一条）" mono field="inner_tensions" value={form.inner_tensions}>
            <textarea className="modal-textarea" rows={2} value={form.inner_tensions} onChange={(e) => update('inner_tensions', e.target.value)} maxLength={LIMITS.inner_tensions.max} />
          </Field>

          <Field label="背景" mono field="background" value={form.background}>
            <textarea className="modal-textarea" rows={3} value={form.background} onChange={(e) => update('background', e.target.value)} maxLength={LIMITS.background.max} />
          </Field>

          <Field label="开场白" mono field="first_message" value={form.first_message}>
            <textarea className="modal-textarea" rows={3} value={form.first_message} onChange={(e) => update('first_message', e.target.value)} maxLength={LIMITS.first_message.max} />
          </Field>

          <Field label="情感模式（每行一条）" mono field="emotional_patterns" value={form.emotional_patterns}>
            <textarea className="modal-textarea" rows={2} value={form.emotional_patterns} onChange={(e) => update('emotional_patterns', e.target.value)} maxLength={LIMITS.emotional_patterns.max} />
          </Field>

          <Field label="决策风格" mono field="decision_style" value={form.decision_style}>
            <textarea className="modal-textarea" rows={2} value={form.decision_style} onChange={(e) => update('decision_style', e.target.value)} maxLength={LIMITS.decision_style.max} />
          </Field>

          <Field label="角色弧线 · 变化轴（一句「从…到…」）" mono field="arc_axis" value={form.arc_axis}>
            <input className="modal-input" value={form.arc_axis} onChange={(e) => update('arc_axis', e.target.value)} maxLength={LIMITS.arc_axis.max} placeholder="从冷漠到学会信任" />
          </Field>
          <ObjectListField legend="角色弧线 · 阶段（按故事顺序）" rows={arcPhases} onChange={edit(setArcPhases)} columns={ARC_COLUMNS} maxCount={ARC_MAX} addLabel="+ 添加阶段" />
          {arcPhases.map((p, i) => (
            <ObjectListField
              key={p._key}
              legend={`第 ${i + 1} 阶段${p.label ? `「${p.label}」` : ''}的做法（只在这一阶段成立）`}
              rows={p.behaviors || []}
              onChange={(rows) => edit(setArcPhases)((ps) => ps.map((q) => (q._key === p._key ? { ...q, behaviors: rows } : q)))}
              columns={BEHAVIOR_COLUMNS}
              maxCount={PHASE_BEHAVIOR_MAX}
              addLabel="+ 添加阶段做法"
            />
          ))}
          <ObjectListField legend="情境→行为（从头到尾都成立的做法）" rows={behaviors} onChange={edit(setBehaviors)} columns={BEHAVIOR_COLUMNS} maxCount={BEHAVIOR_MAX} addLabel="+ 添加一条" />

          <div ref={examplesRef}>
            <Field label="对话示例（每组间空行分隔）" mono field="dialogue_examples" value={form.dialogue_examples}>
              <textarea className="modal-textarea" rows={4} value={form.dialogue_examples} onChange={(e) => update('dialogue_examples', e.target.value)} maxLength={LIMITS.dialogue_examples.max} placeholder="对方：xxx&#10;角色：xxx&#10;&#10;对方：xxx&#10;角色：xxx" />
            </Field>
            {examplesByPhase(data).map(({ index, label, examples }) => (
              <div key={index} className="modal-field">
                <div className="modal-label">第 {index + 1} 阶段{label ? `「${label}」` : ''}下的对话示例（系统从原文挑的，这里只能看）</div>
                <pre className="edit-card-readonly">{examplesToText(examples)}</pre>
              </div>
            ))}
          </div>

          <ObjectListField legend="人物关系" rows={relationships} onChange={edit(setRelationships)} columns={REL_COLUMNS} maxCount={REL_MAX} addLabel="+ 添加关系" />
        </div>

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={close}>取消</button>
          <button type="button" className="btn-primary" onClick={handleSave} disabled={busy}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

// 编辑页顶部关于对话示例的那一行：待补时说明怎么补、给「重新找一次」的按钮；找过之后说结果。
// 只管显示 —— 找不找、找过没有，由表单和它外面那一层定。
function ExamplesNotice({ onRefind, refinding, disabled, refound }) {
  if (onRefind) {
    return (
      <div className="edit-card-notice">
        <span>这张卡还没有对话示例。可以在下面的「对话示例」里自己填；填不出来，可以让系统到原文里重新找一次（只有这一次机会）。</span>
        <button type="button" className="btn-secondary" onClick={onRefind} disabled={disabled}>
          {refinding ? '正在找…' : '重新找一次'}
        </button>
      </div>
    )
  }
  if (refound === true) return <div className="edit-card-notice">找到了对话示例，已经填上，见下面的「对话示例」。</div>
  if (refound === false) return <div className="edit-card-notice">还是没找到。这张卡先不配对话示例，聊天不受影响。</div>
  return null
}

function Field({ label, children, mono, field, value }) {
  const limit = field ? LIMITS[field] : null
  const len = value ? value.length : 0
  const over = limit && len > limit.max
  return (
    <div className="modal-field">
      <label className="modal-label">
        {label}
        {limit && <span className={`field-counter${over ? ' over' : ''}`}>{len}/{limit.max}</span>}
      </label>
      {children}
    </div>
  )
}
