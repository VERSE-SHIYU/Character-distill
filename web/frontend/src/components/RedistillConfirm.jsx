import useAppStore from '../store/useAppStore'
import ConfirmModal from './common/ConfirmModal'

// 重新蒸馏会整张覆盖已有角色卡并重新计费：确认只在 store 的 distillCharacter(force=true) 一处触发，
// 这里只负责把待确认的那一次显示出来。
export default function RedistillConfirm() {
  const prompt = useAppStore((s) => s.redistillPrompt)
  const texts = useAppStore((s) => s.texts)
  const confirmRedistill = useAppStore((s) => s.confirmRedistill)
  const cancelRedistill = useAppStore((s) => s.cancelRedistill)
  if (!prompt) return null
  const text = texts.find((t) => t.id === prompt.textId)
  const book = text ? text.title || text.filename : ''
  return (
    <ConfirmModal
      isOpen
      title={`重新蒸馏「${prompt.name}」？`}
      message={`${book ? `《${book}》：` : ''}会用新结果覆盖现有角色卡，手动修改会丢失；会重新调用模型并产生费用。`}
      confirmText="重新蒸馏"
      onConfirm={confirmRedistill}
      onCancel={cancelRedistill}
      danger
    />
  )
}
