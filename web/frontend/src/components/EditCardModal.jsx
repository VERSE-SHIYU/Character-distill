import { useState } from 'react'

function splitLines(val) {
  return (Array.isArray(val) ? val.join('\n') : val || '')
}

function joinLines(val) {
  return val.split('\n').map((s) => s.trim()).filter(Boolean)
}

const LIMITS = {
  identity: { max: 80 },
  personality_traits: { max: 240, maxLines: 8, perLine: 30 },
  tone: { max: 20 },
  sentence_pattern: { max: 30 },
  catchphrases: { max: 240, maxLines: 6, perLine: 40 },
  vocabulary_level: { max: 20 },
  taboo_words: { max: 120, maxLines: 6, perLine: 20 },
  values: { max: 180, maxLines: 6, perLine: 30 },
  key_memories: { max: 640, maxLines: 8, perLine: 80 },
  inner_tensions: { max: 200, maxLines: 5, perLine: 40 },
  background: { max: 300 },
  first_message: { max: 300 },
  emotional_patterns: { max: 240, maxLines: 6, perLine: 40 },
  decision_style: { max: 50 },
  character_arc: { max: 300, maxLines: 5, perLine: 60 },
  dialogue_examples: { max: 600 },
}

const REL_LIMITS = { target: 20, relation: 20, attitude: 50, maxCount: 8 }

export default function EditCardModal({ isOpen, data, cardId, onSave, onClose }) {
  const style = data.speaking_style || {}
  const rels = data.relationships || []

  const [form, setForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [relationships, setRelationships] = useState([])

  // lazy init on open
  if (isOpen && Object.keys(form).length === 0) {
    const init = {
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
      character_arc: splitLines(data.character_arc),
      dialogue_examples: Array.isArray(data.dialogue_examples)
        ? data.dialogue_examples.join('\n\n')
        : (data.dialogue_examples || ''),
    }
    setForm(init)
    setRelationships(rels.map((r, i) => ({ ...r, _key: i })))
  }

  const update = (field, value) => setForm((f) => ({ ...f, [field]: value }))

  const addRelationship = () => {
    setRelationships((rs) => [...rs, { target: '', relation: '', attitude: '', _key: Date.now() }])
  }

  const updateRel = (idx, field, value) => {
    setRelationships((rs) => rs.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }

  const removeRel = (idx) => {
    setRelationships((rs) => rs.filter((_, i) => i !== idx))
  }

  const FIELD_LABELS = {
    personality_traits: '性格特征',
    catchphrases: '口癖',
    taboo_words: '禁忌用词',
    values: '核心价值观',
    key_memories: '关键记忆',
    inner_tensions: '内在矛盾',
    emotional_patterns: '情感模式',
    character_arc: '角色弧线',
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
      { field: 'character_arc', lines: form.character_arc.split('\n').filter(Boolean) },
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
    if (relationships.length > REL_LIMITS.maxCount) {
      alert(`人物关系最多 ${REL_LIMITS.maxCount} 条`)
      return
    }
    const cardJson = {
      ...data,
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
      character_arc: joinLines(form.character_arc),
      relationships: relationships.map(({ _key, ...r }) => r),
      dialogue_examples: joinLines(form.dialogue_examples.replace(/\n\n+/g, '\n\n')),
    }
    setSaving(true)
    try {
      await onSave(cardJson)
    } finally {
      setSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card edit-card-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">编辑角色卡 — {data.name}</div>

        <div className="edit-form-scroll">
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

          <Field label="角色弧线（每行一个阶段，描述成长变化）" mono field="character_arc" value={form.character_arc}>
            <textarea className="modal-textarea" rows={3} value={form.character_arc} onChange={(e) => update('character_arc', e.target.value)} maxLength={LIMITS.character_arc.max} placeholder="从冷漠到学会信任&#10;从逃避责任到主动担当" />
          </Field>

          <Field label="对话示例（每组间空行分隔）" mono field="dialogue_examples" value={form.dialogue_examples}>
            <textarea className="modal-textarea" rows={4} value={form.dialogue_examples} onChange={(e) => update('dialogue_examples', e.target.value)} maxLength={LIMITS.dialogue_examples.max} placeholder="对方：xxx&#10;角色：xxx&#10;&#10;对方：xxx&#10;角色：xxx" />
          </Field>

          {/* Relationships */}
          <fieldset className="edit-fieldset">
            <legend className="modal-label">人物关系</legend>
            {relationships.map((r, i) => (
              <div key={r._key} className="edit-rel-row">
                <input className="modal-input edit-rel-input" placeholder="对方名字" value={r.target} onChange={(e) => updateRel(i, 'target', e.target.value)} maxLength={REL_LIMITS.target} />
                <input className="modal-input edit-rel-input" placeholder="关系" value={r.relation} onChange={(e) => updateRel(i, 'relation', e.target.value)} maxLength={REL_LIMITS.relation} />
                <input className="modal-input edit-rel-input" placeholder="态度" value={r.attitude} onChange={(e) => updateRel(i, 'attitude', e.target.value)} maxLength={REL_LIMITS.attitude} />
                <button type="button" className="btn-secondary edit-rel-del" onClick={() => removeRel(i)}>✕</button>
              </div>
            ))}
            <button type="button" className="btn-secondary mt-6" onClick={addRelationship} disabled={relationships.length >= REL_LIMITS.maxCount}>+ 添加关系</button>
          </fieldset>
        </div>

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>取消</button>
          <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
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
