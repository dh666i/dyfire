// A remote browser can report an image error while its container is still
// starting. Keep that transient state separate from a real timeout.
export const CLOUD_FRAME_LOAD_TIMEOUT_MS = 30_000;

const remainingIsExpired = remainingSeconds => {
  if (remainingSeconds === null || remainingSeconds === undefined || remainingSeconds === '') return false;
  const value = Number(remainingSeconds);
  return Number.isFinite(value) && value <= 0;
};

export function createCloudFrameState(scanId, now = Date.now()) {
  return {
    scanId: scanId || '',
    status: scanId ? 'loading' : 'idle',
    attempt: 0,
    startedAt: now,
    lastAttemptAt: now,
    error: '',
  };
}

export function frameLoadTimedOut(state, {now = Date.now(), remainingSeconds} = {}) {
  if (!state?.scanId) return false;
  return remainingIsExpired(remainingSeconds)
    || now - state.startedAt >= CLOUD_FRAME_LOAD_TIMEOUT_MS;
}

export function frameLoadError(state, {now = Date.now(), remainingSeconds} = {}) {
  if (!state?.scanId) return createCloudFrameState('', now);
  const attempt = state.attempt + 1;
  if (remainingIsExpired(remainingSeconds)) {
    return {...state, attempt, lastAttemptAt: now, status: 'expired', error: 'expired'};
  }
  if (frameLoadTimedOut(state, {now, remainingSeconds})) {
    return {...state, attempt, lastAttemptAt: now, status: 'timeout', error: 'timeout'};
  }
  return {...state, attempt, lastAttemptAt: now, status: 'loading', error: ''};
}

export function frameLoaded(state, now = Date.now()) {
  if (!state?.scanId) return createCloudFrameState('', now);
  return {...state, status: 'ready', lastAttemptAt: now, error: ''};
}

export function retryCloudFrame(state, now = Date.now()) {
  if (!state?.scanId) return createCloudFrameState('', now);
  // Keep the scan id, but give an intentional retry a fresh frame wait window.
  // The backend session expiry (remainingSeconds) still remains authoritative.
  return {...state, status: 'loading', attempt: 0, startedAt: now, lastAttemptAt: now, error: ''};
}

export function cloudFrameOverlay(state) {
  if (state?.status === 'expired') {
    return {kind: 'error', title: '二维码已失效', detail: '当前扫码会话已过期，请重新发起绑定。'};
  }
  if (state?.status === 'timeout') {
    return {kind: 'error', title: '云端画面加载超时', detail: '登录会话仍在保留，可以继续重试加载画面。'};
  }
  return {
    kind: 'loading',
    title: state?.attempt > 3 ? '正在等待登录环境就绪' : '正在准备安全登录环境',
    detail: '云端容器启动后会自动显示，请稍候，不需要重复点击。',
  };
}
