// 任务状态中文文案只此一处：工作台与角色页共用。
export const STATUS_TEXT = {
  queued: '排队中',
  running: '蒸馏中',
  identifying: '识别角色',
  analyzing: '生成人设',
  merging: '校准语气',
  formatting: '验收上线',
  saving: '保存卡片',
  done: '已完成',
  error: '失败',
  interrupted: '已中断',
}

export const taskLabel = (task) => {
  if (task.progress_pct > 0 && task.status !== 'queued') return `${STATUS_TEXT.running} ${task.progress_pct}%`
  // 无进度的 running 收尾省略号，与改动前的按钮文案一致
  if (task.status === 'running') return `${STATUS_TEXT.running}…`
  return STATUS_TEXT[task.status] || STATUS_TEXT.running
}
