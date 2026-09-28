import * as RS from '@radix-ui/react-select'
import { ChevronDown } from './Icon'

// Radix 把 '' 当作「未选中」显示 placeholder（R4）；调用方的「全部」项取值为 ''，
// 所以进出 Radix 时与哨兵值互相映射。
const EMPTY = '__ui_select_empty__'
const toRadix = (v) => (v === '' ? EMPTY : v)
const fromRadix = (v) => (v === EMPTY ? '' : v)

export default function Select({ value, options, onChange, disabled = false, size = 'md', ariaLabel, className = '' }) {
  return (
    <RS.Root value={toRadix(value)} onValueChange={(v) => onChange(fromRadix(v))} disabled={disabled}>
      <RS.Trigger aria-label={ariaLabel} className={`ui-select ui-select--${size}${className ? ` ${className}` : ''}`}>
        <RS.Value />
        <RS.Icon className="ui-select-caret"><ChevronDown size={size === 'sm' ? 14 : 16} /></RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content position="popper" sideOffset={4} className="ui-select-menu">
          <RS.Viewport>
            {options.map((o) => (
              <RS.Item key={o.value} value={toRadix(o.value)} className="ui-select-option" title={o.label}>
                <RS.ItemText>{o.label}</RS.ItemText>
                <RS.ItemIndicator className="ui-select-check">✓</RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  )
}
