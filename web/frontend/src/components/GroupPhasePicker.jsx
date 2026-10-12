import Avatar from './common/Avatar'
import Select from './common/Select'

// 建群第 2 步「每个角色的时期」列表。纯展示组件：只读 choices，变更通过 onChange(cardId, 阶段号)
// 上报。不读 store、不发请求——choices 的算法（谁是 AI 成员、可选阶段的卡）在调用方。
export default function GroupPhasePicker({ choices, onChange }) {
  return (
    <div className="group-create-card-list">
      {choices.map(({ cardId, card, options, value, avatar }) => (
        <div key={cardId} className="group-row">
          <Avatar name={card.name} size={36} src={avatar} />
          <div className="group-row-info">
            <span className="group-create-card-name">{card.name}</span>
          </div>
          <Select
            size="sm"
            ariaLabel={`${card.name} 的时期`}
            value={String(value)}
            options={options}
            onChange={(v) => onChange(cardId, Number(v))}
          />
        </div>
      ))}
    </div>
  )
}
