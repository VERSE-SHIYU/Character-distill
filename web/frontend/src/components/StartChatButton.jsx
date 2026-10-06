import { useState } from 'react'
import useAppStore from '../store/useAppStore'
import { parseCardJson } from '../utils/card'
import RoleSetupModal from './RoleSetupModal'

// 角色管理「开始对话」与蒸馏工作台「试聊」共用的入口：
// 点按钮 → 身份弹窗（RoleSetupModal）→ 确认/跳过 → 进聊天。
// 存档弹窗不在这里 —— startChat 自己判有没有存档。
// 外观（文案/图标/类名）由调用方传入，两处各自保持原样。
//
// 卡片来源两种：直接给 card，或给 resolveCard 现取（工作台的任务详情还没载入卡片列表）。
export default function StartChatButton({ card, resolveCard, label, icon, className }) {
  const startChat = useAppStore((s) => s.startChat)
  const pushView = useAppStore((s) => s.pushView)
  const [target, setTarget] = useState(null)

  const handleClick = async () => {
    const c = card || (resolveCard ? await resolveCard() : null)
    if (!c) return
    setTarget(c)
  }

  const enterChat = () => {
    setTarget(null)
    pushView('chat')
    startChat(target)
  }

  const data = parseCardJson(target)

  return (
    <>
      <button type="button" className={className} onClick={handleClick}>
        {icon}{label}
      </button>
      <RoleSetupModal
        isOpen={!!target}
        characterName={data.name || target?.name || '?'}
        characterId={target?.id || target?.card_id}
        relationships={data.relationships || []}
        arcPhases={data.character_arc?.phases || []}
        onConfirm={enterChat}
        onSkip={enterChat}
      />
    </>
  )
}
