import useAppStore from '../../store/useAppStore'

// 进入蒸馏工作台的页面级按钮：「创作 → 角色」和「角色管理」头部共用，文案与跳转只在这里定义
export default function DistillWorkbenchButton() {
  const pushView = useAppStore((s) => s.pushView)
  return (
    <button type="button" className="dw-entry-btn" onClick={() => pushView('distillWorkbench')}>
      蒸馏工作台
    </button>
  )
}
