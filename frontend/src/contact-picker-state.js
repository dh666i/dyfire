const SYNC_LIMITS = [20, 40, 60, 80, 100];
const ACTIVE_STATUSES = new Set(['queued', 'running']);
const list = value => Array.isArray(value) ? value.filter(Boolean) : [];
const number = (value, fallback=0) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : fallback;
export const contactKey = item => item?.sec_uid || item?.name;

export function mergeContacts(current, incoming) {
  const merged = new Map();
  [...current, ...incoming].forEach(item => {
    const key = contactKey(item);
    if (key) merged.set(key, {...merged.get(key), ...item});
  });
  return [...merged.values()];
}

export function contactPageMetadata(value={}, itemCount=0) {
  const sync = value.sync || {};
  const active = value.active_sync;
  const activeSync = active?.id && ACTIVE_STATUSES.has(active.status) ? active : null;
  const total = number(value.total, Math.max(itemCount, number(value.next_offset)));
  const syncLimit = number(value.sync_limit ?? (sync.status === 'succeeded' ? sync.limit : 0));
  const syncHasMore = typeof value.sync_has_more === 'boolean' ? value.sync_has_more
    : sync.status === 'succeeded' && typeof sync.has_more === 'boolean' ? sync.has_more : null;
  // Old caches often have no retained job. Missing evidence is not an exhausted
  // Douyin directory, so users must still have a way to request a fresh sync.
  const canSyncMore = activeSync ? false : typeof value.can_sync_more === 'boolean'
    ? value.can_sync_more : syncHasMore == null || (syncHasMore && syncLimit < 100);
  const inferredLimit = SYNC_LIMITS.find(limit => limit > Math.max(total, syncLimit)) || 100;
  const nextLimit = SYNC_LIMITS.includes(Number(value.sync_next_limit))
    ? Number(value.sync_next_limit) : inferredLimit;
  const refreshLimit = SYNC_LIMITS.find(limit => limit >= Math.max(total, syncLimit, 20)) || 100;
  return {
    dbHasMore: Boolean(value.has_more), nextOffset: number(value.next_offset, itemCount),
    total, syncHasMore, syncLimit, canSyncMore, nextLimit, refreshLimit, activeSync,
    syncStatus: sync.status || '',
    streakDiagnostics: sync.streak_diagnostics && typeof sync.streak_diagnostics === 'object'
      ? sync.streak_diagnostics : null,
  };
}

function abortableDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, {once:true});
    if (signal.aborted) abort();
  });
}

const emptyState = () => ({items:[], selected:new Set(), loading:false, busy:'', error:'', page:contactPageMetadata()});

// The controller owns the asynchronous picker session. Closing or switching
// accounts invalidates every response, including transports that ignore abort.
export function createContactPickerController({request, wait=abortableDelay, now=Date.now, pollMs=1500, jobTimeoutMs=300000}) {
  let state = emptyState();
  let session = null;
  const listeners = new Set();
  const emit = patch => { state = {...state, ...patch}; listeners.forEach(listener => listener(state)); };
  const current = token => token === session && !token.controller.signal.aborted;
  const path = token => `/accounts/${encodeURIComponent(token.accountId)}`;
  const get = (token, suffix, options={}) => request(`${path(token)}${suffix}`, {...options, signal:token.controller.signal});
  const applyPage = (token, value) => {
    if (!current(token)) return;
    emit({items:mergeContacts(state.items, list(value?.items)), page:contactPageMetadata(value, list(value?.items).length)});
  };
  const readPage = async (token, offset=0) => {
    const value = await get(token, `/conversations?offset=${offset}&limit=100`);
    applyPage(token, value);
    return value;
  };
  const followJob = async (token, job) => {
    let status = job;
    const deadline = now() + jobTimeoutMs;
    while (current(token) && ACTIVE_STATUSES.has(status?.status)) {
      const remaining = deadline - now();
      if (remaining <= 0) throw new Error('好友同步仍未完成，请稍后重新打开查看；已选好友已保留。');
      await wait(Math.min(pollMs, remaining), token.controller.signal);
      if (!current(token)) return;
      status = await get(token, `/directory-jobs/${encodeURIComponent(job.id)}`);
    }
    if (!current(token)) return;
    if (status?.status !== 'succeeded') {
      emit({page:{...state.page, activeSync:null, canSyncMore:state.page.syncHasMore == null || (state.page.syncHasMore && state.page.syncLimit < 100)}});
      throw new Error(status?.error || '好友同步未完成，请重试');
    }
    await readPage(token);
  };
  const report = (token, error) => {
    if (current(token) && error?.name !== 'AbortError') emit({error:error?.message || '好友加载失败，请重试'});
  };
  const resume = async (token, job) => {
    emit({busy:'sync', error:''});
    try { await followJob(token, job); }
    catch (error) { report(token, error); }
    finally { if (current(token)) emit({busy:''}); }
  };
  const load = async () => {
    const token = session;
    if (!token || state.loading || state.busy) return;
    emit({loading:true, error:''});
    try {
      await readPage(token);
      if (!current(token)) return;
      emit({loading:false});
      if (state.page.activeSync) await resume(token, state.page.activeSync);
    } catch (error) { report(token, error); }
    finally { if (current(token)) emit({loading:false}); }
  };
  const close = () => { session?.controller.abort(); session = null; };
  return {
    getState: () => state,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    close,
    open: (accountId, csrf='') => {
      close();
      session = {accountId, csrf, controller:new AbortController()};
      emit(emptyState());
      return load();
    },
    retry: load,
    select: (key, checked) => {
      const selected = new Set(state.selected);
      checked ? selected.add(key) : selected.delete(key);
      emit({selected});
    },
    selectMany: keys => emit({selected:new Set([...state.selected, ...keys])}),
    clearSelection: () => emit({selected:new Set()}),
    next: async () => {
      const token = session;
      if (!token || state.loading || state.busy) return;
      const page = state.page;
      emit({busy:page.dbHasMore ? 'page' : 'sync', error:''});
      try {
        if (page.dbHasMore) await readPage(token, page.nextOffset);
        else if (page.activeSync) await followJob(token, page.activeSync);
        else {
          const limit = page.canSyncMore ? page.nextLimit : page.refreshLimit;
          const job = await get(token, '/directory-jobs', {
            method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'},
            body:new URLSearchParams({csrf_token:token.csrf, limit:String(limit), query:''}),
          });
          if (current(token)) {
            if (job?.id && ACTIVE_STATUSES.has(job.status)) emit({page:{...state.page, activeSync:job}});
            await followJob(token, job);
          }
        }
      } catch (error) { report(token, error); }
      finally { if (current(token)) emit({busy:''}); }
    },
  };
}
