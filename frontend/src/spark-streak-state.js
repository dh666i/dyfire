import {contactKey} from './contact-picker-state.js';

const states = new Set(['unknown', 'active', 'broken', 'recovering']);
const list = value => Array.isArray(value) ? value.filter(Boolean) : [];

export function streakSnapshot(value) {
  const source = value && typeof value === 'object' ? value : {};
  const observed = typeof source.observed_at === 'string' && Boolean(streakDate(source.observed_at));
  const days = observed && typeof source.days === 'number' && Number.isSafeInteger(source.days) && source.days >= 0 ? source.days : null;
  return {...source, days, state: observed && states.has(source.state) ? source.state : 'unknown', observed_at: observed ? source.observed_at : null, stale: Boolean(source.stale)};
}

function streakDate(value) {
  if (!value || typeof value !== 'string') return null;
  // API timestamps without an offset are UTC, matching persisted observations.
  const normalized = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value) ? value.replace(' ', 'T') + 'Z' : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function streakTime(value, full = false) {
  const date = streakDate(value);
  if (!date) return '尚未同步';
  return date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', ...(full ? {year:'numeric'} : {}), month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
}

export function streakStateLabel(value) {
  const snapshot = streakSnapshot(value);
  const label = {active:'已点亮', broken:'已熄灭', recovering:'重燃中', unknown:'状态待确认'}[snapshot.state];
  return snapshot.stale && snapshot.state !== 'unknown' ? `上次${label}` : label;
}

export function streakSummary(value) {
  const snapshot = streakSnapshot(value);
  return `${snapshot.days === null ? '天数待同步' : `${snapshot.days} 天`} · ${streakStateLabel(snapshot)}`;
}

export function filterStreakContacts(items, {query='', filter='all', order='default', existing=new Set()}={}) {
  const search = query.trim().toLocaleLowerCase();
  const filtered = list(items).filter(item => {
    if (search && ![item.name,item.nickname,item.remark_name,item.unique_id,item.short_id].filter(Boolean).join(' ').toLocaleLowerCase().includes(search)) return false;
    const snapshot = streakSnapshot(item.spark);
    if (filter === 'without_task') return !existing.has(contactKey(item));
    if (filter === 'has_streak') return snapshot.days !== null && snapshot.days > 0;
    if (filter === 'active') return snapshot.state === 'active' && !snapshot.stale;
    if (filter === 'needs_attention') return ['broken','recovering'].includes(snapshot.state);
    return true;
  });
  if (order === 'days_desc') filtered.sort((a,b) => (streakSnapshot(b.spark).days ?? -1) - (streakSnapshot(a.spark).days ?? -1));
  if (order === 'name') filtered.sort((a,b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'));
  return filtered;
}

export function streakMilestones(value) {
  return list(value).filter(item => Number.isSafeInteger(item.days) && item.days > 0 && ['first_observed','crossed'].includes(item.kind) && streakDate(item.observed_at));
}

export function streakHistoryPath(target, offset = 0) {
  const accountId = target?.accountId || target?.account_id;
  if (!accountId || !target?.sec_uid) return null;
  const query = new URLSearchParams({account_id:accountId,sec_uid:target.sec_uid,offset:String(offset),limit:'30'});
  return `/api/streaks/history?${query}`;
}
