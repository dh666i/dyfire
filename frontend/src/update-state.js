const phaseLabels = {
  idle: '等待操作', accepted: '已接受', checking: '检查版本', checked: '检查完成',
  pulling: '下载镜像', web_pulling: '下载网页', building: '准备版本', stopping: '等待服务退出',
  installing: '启动新版本', web_installing: '切换网页', web_candidate: '验证新网页',
  rollback: '恢复版本', rollback_pulling: '下载回滚版本', rollback_failed: '恢复原版本',
  web_rollback: '恢复原网页', web_restoring: '恢复原网页', recovering: '恢复更新状态',
  complete: '更新完成', web_complete: '网页更新完成', rollback_complete: '回滚完成',
  rolled_back: '已恢复原版本', failed: '更新未完成', web_failed: '网页更新未完成',
};
const terminalPhases = new Set(['idle', 'checked', 'complete', 'web_complete', 'rollback_complete', 'rolled_back', 'failed', 'web_failed']);

export function updatePhaseLabel(phase) {
  return phaseLabels[phase] || '更新处理中';
}

export function isUpdateBusy(phase) {
  return Boolean(phase) && !terminalPhases.has(phase);
}

export function updateAvailability(update = {}) {
  const web = update.component_versions?.web || update.web_current || update.current || '—';
  const browser = update.component_versions?.browser || update.installed || update.current || '—';
  const latest = update.latest || update.available || web;
  return {
    web, browser, latest,
    fullAvailable: Boolean(update.update_available),
    webAvailable: Boolean(update.web_update_available ?? update.update_available),
    webAllowed: update.web_update_allowed !== false,
  };
}

export function progressValue(value) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : undefined;
}

export function formatUpdateBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${Math.round(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}
