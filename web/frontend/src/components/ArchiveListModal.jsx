import useAppStore from '../store/useAppStore'
import { formatChatTime } from '../utils/time'

function fmtTime(iso) {
  if (!iso) return '—'
  try { return formatChatTime(iso) } catch { return iso }
}

function previewText(text, max = 60) {
  if (!text) return '暂无消息'
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}…` : one
}

function affinityStage(affinity) {
  const a = affinity ?? 50
  if (a >= 80) return { label: '亲密', cls: 'stage-close' }
  if (a >= 60) return { label: '信任', cls: 'stage-trust' }
  if (a >= 40) return { label: '友好', cls: 'stage-friendly' }
  if (a >= 20) return { label: '疏远', cls: 'stage-distant' }
  return { label: '戒备', cls: 'stage-guard' }
}

export default function ArchiveListModal() {
  const archiveModalOpen = useAppStore((s) => s.archiveModalOpen)
  const archiveList = useAppStore((s) => s.archiveList)
  const pendingCard = useAppStore((s) => s.pendingCard)
  const enterArchive = useAppStore((s) => s.enterArchive)
  const createNewArchive = useAppStore((s) => s.createNewArchive)

  const close = () => {
    useAppStore.setState({ archiveModalOpen: false, archiveList: [], pendingCard: null })
  }

  if (!archiveModalOpen) return null

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal-card archive-list-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">选择存档 — {pendingCard?.name || '?'}</div>

        <button
          type="button"
          className="archive-new-btn"
          onClick={createNewArchive}
        >
          + 新建存档
        </button>

        <div className="archive-list-scroll">
          {archiveList.map((s) => {
            const stage = affinityStage(s.affinity)
            return (
              <button
                key={s.id}
                type="button"
                className="history-item archive-slot-item"
                onClick={() => enterArchive(s)}
              >
                <div className="history-item-body">
                  <div className="history-item-head">
                    <div className="history-item-name-row">
                      <span className={`archive-stage-tag ${stage.cls}`}>{stage.label}</span>
                      <span className="archive-affinity-nums">
                        <span title="好感">♡{s.affinity ?? 50}</span>
                        <span title="信任">信任{s.trust ?? 30}</span>
                        <span title="防御">防御{s.guard ?? 70}</span>
                      </span>
                    </div>
                    <span className="history-item-time">{fmtTime(s.last_message_at || s.updated_at)}</span>
                  </div>
                  <p className="history-item-preview">{previewText(s.last_message)}</p>
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
