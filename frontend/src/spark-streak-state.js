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

export function streakSyncHint(diagnostics) {
  if (!diagnostics || typeof diagnostics !== 'object') return '';
  const matched = Number(diagnostics.matched_observations) || 0;
  if (matched > 0) return `最近一次同步读取到 ${matched} 位好友的火花。`;
  if (diagnostics.trusted_self_uid_available === false) return '好友已同步，但账号身份未完成核验，暂时无法匹配火花。';
  const candidates = Number(diagnostics.init_observations) || 0;
  const selfMatches = Number(diagnostics.self_participant_matches) || 0;
  const peerMatches = Number(diagnostics.peer_uid_matches) || 0;
  if (candidates > 0 && selfMatches === 0) return `抖音返回了 ${candidates} 条火花信息，但没有匹配到当前抖音账号身份。`;
  if (selfMatches > 0 && peerMatches === 0) return `已匹配到 ${selfMatches} 条本人会话，但好友身份未匹配，火花暂未显示。`;
  if (peerMatches > 0) return `已匹配到 ${peerMatches} 位好友会话，但平台火花信息无法通过一致性校验。`;
  if (Number(diagnostics.init_ok_responses) > 0) return '好友已同步；抖音本次返回的会话中没有可识别的火花信息。';
  return '';
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
