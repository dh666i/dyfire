export const FEEDBACK_DURATION = 4200;
export const FEEDBACK_LIFETIME = Symbol('feedbackLifetime');

// Sonner owns rendering and dismissal animation. Keep a separate lifetime so
// stack expansion, queue updates and focus restoration cannot restart a clock.
export function feedbackOptions(options = {}) {
  const lifetime = {duration: options.duration ?? FEEDBACK_DURATION, closed: false};
  const finish = (callback, notice) => {
    if (lifetime.closed) return;
    lifetime.closed = true;
    callback?.(notice);
  };
  return {
    ...options,
    duration: Infinity,
    [FEEDBACK_LIFETIME]: lifetime,
    onDismiss: notice => finish(options.onDismiss, notice),
    onAutoClose: notice => finish(options.onAutoClose, notice),
  };
}

export function createFeedbackTimers({now, schedule, cancel, onExpire}) {
  const entries = new Map();
  let paused = false;
  function stop(entry) {
    if (entry.timer === null) return;
    cancel(entry.timer);
    entry.timer = null;
    entry.remaining = Math.max(0, entry.remaining - (now() - entry.started));
  }
  function start(entry) {
    if (paused || entry.timer !== null || entry.expired || !Number.isFinite(entry.remaining)) return;
    entry.started = now();
    entry.timer = schedule(() => {
      if (entries.get(entry.notice.id) !== entry) return;
      entry.timer = null;
      entry.remaining = 0;
      entry.expired = true;
      onExpire(entry.notice);
    }, entry.remaining);
  }
  return {
    sync(notices) {
      const ids = new Set(notices.map(notice => notice.id));
      for (const [id, entry] of entries) if (!ids.has(id)) { stop(entry); entries.delete(id); }
      for (const notice of notices) {
        const lifetime = notice[FEEDBACK_LIFETIME];
        const duration = notice.type === 'loading' ? Infinity : (lifetime?.duration ?? FEEDBACK_DURATION);
        let entry = entries.get(notice.id);
        // Unrelated queue changes keep remaining time. A new message for the
        // same id (including loading -> success) gets its own reading time.
        if (!entry || entry.lifetime !== lifetime || entry.duration !== duration) {
          if (entry) stop(entry);
          entry = {notice, lifetime, duration, remaining: duration, timer: null, started: 0, expired: false};
          entries.set(notice.id, entry);
        } else entry.notice = notice;
        start(entry);
      }
    },
    setPaused(value) {
      if (paused === value) return;
      paused = value;
      for (const entry of entries.values()) value ? stop(entry) : start(entry);
    },
    clear() {
      for (const entry of entries.values()) stop(entry);
      entries.clear();
    },
  };
}
