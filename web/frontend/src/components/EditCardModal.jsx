import { useState } from 'react'

function splitLines(val) {
  return (Array.isArray(val) ? val.join('\n') : val || '')
}

function joinLines(val) {
  return val.split('\n').map((s) => s.trim()).filter(Boolean)
}

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

  const handleSave = async () => {
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
          <Field label="一句话身份" mono>
            <input className="modal-input" value={form.identity} onChange={(e) => update('identity', e.target.value)} />
          </Field>

          <Field label="性格特征（每行一条）" mono>
            <textarea className="modal-textarea" rows={3} value={form.personality_traits} onChange={(e) => update('personality_traits', e.target.value)} />
          </Field>

          <Field label="语气" mono>
            <input className="modal-input" value={form.tone} onChange={(e) => update('tone', e.target.value)} />
          </Field>

          <Field label="句式特征" mono>
            <input className="modal-input" value={form.sentence_pattern} onChange={(e) => update('sentence_pattern', e.target.value)} />
          </Field>

          <Field label="口癖（每行一条）" mono>
            <textarea className="modal-textarea" rows={2} value={form.catchphrases} onChange={(e) => update('catchphrases', e.target.value)} />
          </Field>

          <Field label="用词水平" mono>
            <input className="modal-input" value={form.vocabulary_level} onChange={(e) => update('vocabulary_level', e.target.value)} />
          </Field>

          <Field label="禁忌用词（每行一条）" mono>
            <textarea className="modal-textarea" rows={2} value={form.taboo_words} onChange={(e) => update('taboo_words', e.target.value)} />
          </Field>

          <Field label="核心价值观（每行一条）" mono>
            <textarea className="modal-textarea" rows={2} value={form.values} onChange={(e) => update('values', e.target.value)} />
          </Field>

          <Field label="关键记忆（每行一条）" mono>
            <textarea className="modal-textarea" rows={3} value={form.key_memories} onChange={(e) => update('key_memories', e.target.value)} />
          </Field>

          <Field label="内在矛盾（每行一条）" mono>
            <textarea className="modal-textarea" rows={2} value={form.inner_tensions} onChange={(e) => update('inner_tensions', e.target.value)} />
          </Field>

          <Field label="背景" mono>
            <textarea className="modal-textarea" rows={3} value={form.background} onChange={(e) => update('background', e.target.value)} />
          </Field>

          <Field label="开场白" mono>
            <textarea className="modal-textarea" rows={3} value={form.first_message} onChange={(e) => update('first_message', e.target.value)} />
          </Field>

          <Field label="情感模式（每行一条）" mono>
            <textarea className="modal-textarea" rows={2} value={form.emotional_patterns} onChange={(e) => update('emotional_patterns', e.target.value)} />
          </Field>

          <Field label="决策风格" mono>
            <textarea className="modal-textarea" rows={2} value={form.decision_style} onChange={(e) => update('decision_style', e.target.value)} />
          </Field>

          <Field label="角色弧线（每行一个阶段，描述成长变化）" mono>
            <textarea className="modal-textarea" rows={3} value={form.character_arc} onChange={(e) => update('character_arc', e.target.value)} placeholder="从冷漠到学会信任&#10;从逃避责任到主动担当" />
          </Field>

          <Field label="对话示例（每组间空行分隔）" mono>
            <textarea className="modal-textarea" rows={4} value={form.dialogue_examples} onChange={(e) => update('dialogue_examples', e.target.value)} placeholder="对方：xxx&#10;角色：xxx&#10;&#10;对方：xxx&#10;角色：xxx" />
          </Field>

          {/* Relationships */}
          <fieldset className="edit-fieldset">
            <legend className="modal-label">人物关系</legend>
            {relationships.map((r, i) => (
              <div key={r._key} className="edit-rel-row">
                <input className="modal-input edit-rel-input" placeholder="对方名字" value={r.target} onChange={(e) => updateRel(i, 'target', e.target.value)} />
                <input className="modal-input edit-rel-input" placeholder="关系" value={r.relation} onChange={(e) => updateRel(i, 'relation', e.target.value)} />
                <input className="modal-input edit-rel-input" placeholder="态度" value={r.attitude} onChange={(e) => updateRel(i, 'attitude', e.target.value)} />
                <button type="button" className="btn-secondary edit-rel-del" onClick={() => removeRel(i)}>✕</button>
              </div>
            ))}
            <button type="button" className="btn-secondary" onClick={addRelationship} style={{ marginTop: 6 }}>+ 添加关系</button>
          </fieldset>
        </div>

        <div className="modal-actions">
          <button type="button" className="btn-secondary glass" onClick={onClose}>取消</button>
          <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children, mono }) {
  return (
    <div className="modal-field">
      <label className="modal-label">{label}</label>
      {children}
    </div>
  )
}
