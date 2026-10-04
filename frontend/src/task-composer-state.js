const list = value => Array.isArray(value) ? value : [];
const timePattern = /^([01]\d|2[0-3]):[0-5]\d$/;

export function isReadySchedule(plan, count) {
  return plan?.status === 'ready' && !plan.blocked && list(plan.times).length === count
    && plan.times.every(time => timePattern.test(time))
    && list(plan.entries).length === count
    && plan.entries.every((entry, index) => entry.time === plan.times[index] && Boolean(entry.next_run_at));
}

export function mergeTaskRows(current, incoming) {
  const rows = new Map();
  [...list(incoming), ...list(current)].forEach(item => {
    if (item?.id && !rows.has(item.id)) rows.set(item.id, item);
  });
  return [...rows.values()];
}

// Own the preview's request lifecycle so even a transport that ignores abort
// cannot overwrite a newer account, selection or manually edited time.
export function createTaskComposerController({request, refresh, onCreated, debounceMs=250, setTimer=setTimeout, clearTimer=clearTimeout}) {
  let state = {status:'idle', plan:null, error:'', conflict:'', creating:false, createError:'', success:null};
  let config = null;
  let generation = 0;
  let pending = null;
  let lastCommittedKey = '';
  const listeners = new Set();
  const emit = patch => { state = {...state, ...patch}; listeners.forEach(listener => listener(state)); };
  const invalidate = () => {
    generation++;
    if (pending?.timer != null) clearTimer(pending.timer);
    pending?.controller.abort();
    pending = null;
  };
  const preview = async token => {
    const params = new URLSearchParams({account_id:token.config.accountId, count:String(token.config.count), mode:token.config.mode});
    if (token.config.mode === 'manual') params.set('preferred', token.config.preferred || '');
    try {
      const plan = token.config.targets.length
        ? await request('/tasks/preview', {method:'POST', headers:{'Content-Type':'application/json'}, signal:token.controller.signal,
          body:JSON.stringify({csrf_token:token.config.csrf, account_id:token.config.accountId, targets:token.config.targets, mode:token.config.mode, time:token.config.preferred})})
        : await request(`/api/studio/schedule?${params}`, {signal:token.controller.signal});
      if (token.generation !== generation || token.controller.signal.aborted) return;
      const plannedCount = token.config.targets.length ? list(plan?.targets).length : token.config.count;
      if (!plannedCount || !isReadySchedule(plan, plannedCount)) {
        emit({status:'unavailable', plan:null, error:plan?.blocked?'当前抖音账号需要重新验证，请先到抖音账号页面处理。':'暂时无法安排发送时间，请稍后重试。'});
        return;
      }
      emit({status:'ready', plan, error:''});
    } catch (error) {
      if (token.generation === generation && !token.controller.signal.aborted) {
        emit({status:'error', plan:null, error:error?.message || '发送时间安排失败，请重试。'});
      }
    }
  };
  const configure = (value, force=false) => {
    const next = {accountId:value.accountId || '', count:Math.max(1, Number(value.count) || 1), targets:list(value.targets), csrf:value.csrf || '', mode:value.mode === 'manual' ? 'manual' : 'auto', preferred:value.preferred || ''};
    if (!force && JSON.stringify(next) === JSON.stringify(config)) return;
    invalidate();
    config = next;
    // A changed account, target set, mode or time is a new intent. The
    // duplicate guard only protects a committed POST while refresh is failing.
    lastCommittedKey = '';
    if (!next.accountId) return emit({status:'idle', plan:null, error:'', conflict:''});
    // There is no composer preview to show until the user selects a friend.
    // Avoid a speculative one-friend request with a fabricated time.
    if (!next.targets.length) return emit({status:'idle', plan:null, error:'', conflict:'', createError:''});
    if (next.mode === 'manual' && !timePattern.test(next.preferred)) return emit({status:'unavailable', plan:null, error:'请填写有效的每日发送时间。', conflict:''});
    emit({status:'loading', plan:null, error:'', conflict:'', createError:''});
    const token = {config:next, generation, controller:new AbortController(), timer:null};
    pending = token;
    token.timer = setTimer(() => { token.timer = null; void preview(token); }, debounceMs);
  };
  return {
    getState:() => state,
    subscribe:listener => { listeners.add(listener); return () => listeners.delete(listener); },
    configure,
    retry:() => config && configure(config, true),
    close:invalidate,
    create:async ({payload, csrf}) => {
      if (state.creating) return null;
      const plannedCount = config?.targets.length ? list(state.plan?.targets).length : config?.count;
      // The preview normalizes duplicate display names and aliases. Compare
      // with the selected target set, while using the normalized plan count
      // for the actual schedule rows.
      if (!config || payload.account_id !== config.accountId || list(payload.targets).length !== config.targets.length || !isReadySchedule(state.plan, plannedCount) || state.status !== 'ready') {
        emit({createError:'请先等待发送时间安排完成，再确认创建。'});
        return null;
      }
      const confirmedPlan = state.plan;
      const normalizedPayload = {...payload, targets:confirmedPlan.targets || payload.targets};
      const actualCount = list(normalizedPayload.targets).length;
      if (!actualCount || actualCount !== plannedCount) {
        emit({createError:'好友选择已发生变化，请重新核对发送时间安排。'});
        return null;
      }
      const createKey = JSON.stringify({payload:normalizedPayload, mode:config.mode, times:confirmedPlan.times});
      if (createKey === lastCommittedKey) return null;
      const createGeneration = generation;
      emit({creating:true, createError:'', conflict:''});
      let result;
      try {
        result = await request('/api/studio/tasks', {
          method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf},
          body:JSON.stringify({...normalizedPayload, send_time:confirmedPlan.times[0], schedule_mode:config.mode,
            expected_times:confirmedPlan.times, expected_next_runs:confirmedPlan.entries.map(entry => entry.next_run_at)}),
        });
      } catch (error) {
        const detail = error?.payload?.detail || error?.payload || {};
        if (error?.status === 409 && detail.code === 'schedule_changed' && createGeneration === generation) {
          const updatedPlan = {...detail.plan, targets:detail.plan?.targets || confirmedPlan.targets};
          const valid = isReadySchedule(updatedPlan, plannedCount);
          emit({status:valid?'ready':'unavailable', plan:valid?updatedPlan:null,
            error:valid?'':'暂时无法安排发送时间，请稍后重试。',
            conflict:'发送时间发生变化，请确认新的安排后再次创建。'});
        } else {
          emit({createError:error?.message || '任务创建失败，请稍后重试。'});
        }
        emit({creating:false});
        return null;
      }
      // A committed POST is successful even if refreshing the surrounding app
      // fails. Never expose a resubmit action for that refresh failure.
      const success = {items:list(result?.items), entries:list(result?.entries), times:list(result?.times), count:result?.count ?? list(result?.items).length, refreshError:''};
      lastCommittedKey = createKey;
      emit({success, creating:false, createError:'', conflict:''});
      onCreated?.(result);
      try { if (refresh && await refresh() === null) throw new Error('列表刷新失败，请稍后重试。'); }
      catch (error) { if (state.success === success) emit({success:{...success, refreshError:error?.message || '列表刷新失败，请稍后重试。'}}); }
      return result;
    },
  };
}
