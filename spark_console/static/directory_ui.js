(() => {
  'use strict';
  const account = document.querySelector('#task-account, #account');
  const csrf = document.querySelector('[name="csrf_token"]')?.value || document.querySelector('[data-csrf-token]')?.dataset.csrfToken || document.querySelector('#create-form')?.dataset.csrf;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let leaving = false;
  window.addEventListener('pagehide', () => { leaving = true; });

  async function json(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options, signal: controller.signal});
      if (response.redirected) throw new Error('登录状态已失效，请重新登录');
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请稍后重试');
      return data;
    } finally { clearTimeout(timer); }
  }

  function controls(host, getAccount, getQuery, profileOnly = false) {
    const bar = document.createElement('div');
    bar.className = 'directory-controls';
    const actions = document.createElement('div');
    actions.className = 'directory-actions';
    const status = document.createElement('p');
    status.className = 'directory-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    let currentLimit = 20, version = 0, busy = false;
    const buttons = [];
    function button(label, action) {
      const el = document.createElement('button');
      el.type = 'button'; el.className = 'quiet'; el.textContent = label;
      el.addEventListener('click', action); actions.append(el); buttons.push(el); return el;
    }
    function setBusy(value) {
      busy = value;
      buttons.forEach(el => { el.disabled = value; });
      bar.setAttribute('aria-busy', String(value));
      buttons.forEach(el => el.classList.toggle('is-loading', value && el === buttons[0]));
      if (more && currentLimit >= 100) more.disabled = true;
    }
    async function poll(id, job, token) {
      const start = Date.now();
      while (['queued', 'running'].includes(job.status)) {
        if (leaving || token !== version || getAccount() !== id) return null;
        if (Date.now() - start > 305000) throw new Error('等待超时，可重新读取状态后重试');
        status.textContent = job.status === 'queued' ? '已排队，等待云端空闲…' : '正在从抖音读取资料和好友…';
        await sleep(1800);
        job = await json(`/accounts/${encodeURIComponent(id)}/directory-jobs/${encodeURIComponent(job.id)}`);
      }
      if (job.status !== 'succeeded') throw new Error(job.error || '同步未完成，请稍后重试');
      return job;
    }
    async function run(limit, query = '', existing = null) {
      if (busy) return;
      const id = getAccount(), token = ++version;
      if (!id) return;
      setBusy(true); status.textContent = '正在提交同步请求…';
      bar.querySelector('.directory-diagnostic')?.remove();
      try {
        const job = await poll(id, existing || await json(`/accounts/${encodeURIComponent(id)}/directory-jobs`, {
          method: 'POST', body: new URLSearchParams({csrf_token: csrf || '', limit: String(limit), query})
        }), token);
        if (!job || token !== version || id !== getAccount()) return;
        if (!query) currentLimit = job.limit;
        status.textContent = `同步完成，读取 ${job.items?.length || 0} 个最近会话 · ${new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})}${profileOnly ? '。刷新页面查看最新昵称和头像。' : ''}`;
        document.dispatchEvent(new CustomEvent('directory-updated', {detail: {accountId: id, query, items: job.items}}));
        if (more && !query) more.hidden = !job.has_more;
      } catch (error) {
        if (token === version && id === getAccount()) {
          const technical=/HTTP |业务码|页面就绪|会话列表/.test(error.message);
          status.textContent = error.name === 'AbortError' ? '网络超时，请稍后重试' : technical ? '本次同步未完成，已保存的会话仍可筛选，请稍后重试。' : error.message;
          bar.querySelector('.directory-diagnostic')?.remove();
          if(technical){const detail=document.createElement('details');detail.className='directory-diagnostic';const summary=document.createElement('summary');summary.textContent='诊断详情';const text=document.createElement('small');text.textContent=error.message;detail.append(summary,text);bar.append(detail);}
        }
      } finally { if (token === version) setBusy(false); }
    }
    button(profileOnly ? '更新资料与好友' : '同步最近 20 个', () => run(20));
    const more = profileOnly ? null : button('加载更多', () => run(Math.min(100, currentLimit + 20)));
    // Both pickers filter as the user types; no redundant search button.
    bar.append(actions, status); host.append(bar);
    async function resume() {
      const id = getAccount(), token = ++version;
      currentLimit = 20; setBusy(false); status.textContent = '';
      if (more) more.hidden = false;
      try {
        const data = await json(`/accounts/${encodeURIComponent(id)}/conversations`);
        if (token !== version || id !== getAccount()) return;
        currentLimit = data.sync?.limit || 20;
        if (more) { more.hidden = data.sync ? !data.sync.has_more : false; more.disabled = currentLimit >= 100; }
        status.textContent = data.sync?.finished_at ? `最近同步：${new Date(data.sync.finished_at).toLocaleString('zh-CN')} · 搜索仅限已同步会话` : '可先同步最近会话，搜索仅限已同步内容';
        if (data.active_sync && !data.active_sync.query) run(data.active_sync.limit, '', data.active_sync);
      } catch (_) { if (token === version) status.textContent = '可点击同步更新好友列表'; }
    }
    if (account && !profileOnly) account.addEventListener('change', resume);
    resume();
  }

  if (account && csrf) {
    const studioToolbar = document.querySelector('#friends-dialog .selection-tools');
    if (studioToolbar) {
      const input=document.querySelector('#friend-search');if(input)input.placeholder='搜索已同步昵称、备注或抖音号';
      const host = document.createElement('div'); studioToolbar.before(host);
      controls(host, () => account.value, () => document.querySelector('#friend-search').value);
    }
    const toolbar = document.querySelector('.target-picker-toolbar');
    const single = document.querySelector('#task-target-options');
    if (toolbar) {
      const host = document.createElement('div'); toolbar.after(host);
      const search = document.querySelector('#target-search-input');
      search.placeholder = '搜索已同步昵称、备注或抖音号';
      controls(host, () => account.value, () => search.value);
    } else if (single) {
      const host = document.createElement('div'); single.parentElement.after(host);
      controls(host, () => account.value, () => document.querySelector('#task-target').value);
    }
  }
  document.querySelectorAll('[data-profile-sync]').forEach(host => controls(host, () => host.dataset.profileSync, null, true));

  function avatars(root = document) {
    root.querySelectorAll('[data-avatar-image]').forEach(img => {
      img.addEventListener('error', () => img.remove(), {once: true});
      if (img.complete && !img.naturalWidth) img.remove();
    });
  }
  avatars();
  document.querySelectorAll('[data-task-group]').forEach(group => {
    const content = group.querySelector('[data-group-content]');
    let loaded = false, loading = false;
    async function load(url) {
      if (loading) return;
      loading = true; content.setAttribute('aria-busy', 'true');
      if (!loaded) content.innerHTML = '<div class="ui-load-placeholder" role="status" aria-label="正在读取任务"><span></span><span></span><span></span></div>';
      try {
        const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok || response.redirected) throw new Error('任务读取失败，请重新登录或重试');
        // Authenticated same-origin server-rendered partial; all task/user values are Jinja-escaped.
        content.innerHTML = await response.text();
        loaded = true; avatars(content);
        document.dispatchEvent(new CustomEvent('task-groups-loaded'));
      } catch (error) {
        const p = document.createElement('p'); p.className = 'directory-status'; p.textContent = error.message;
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'quiet'; retry.textContent = '重试';
        retry.addEventListener('click', () => load(url));
        content.querySelector('.ui-inline-feedback')?.remove();
        if (loaded) { const feedback = document.createElement('div'); feedback.className = 'ui-inline-feedback'; feedback.setAttribute('role','alert'); feedback.append(p,retry); content.append(feedback); }
        else content.replaceChildren(p, retry);
      } finally { loading = false; content.removeAttribute('aria-busy'); }
    }
    group.addEventListener('toggle', () => { if (group.open && !loaded) load(group.dataset.groupUrl); });
    content.addEventListener('click', event => {
      const link = event.target.closest('.admin-pagination a');
      if (link) { event.preventDefault(); load(link.href); }
    });
    if (group.open) load(group.dataset.groupUrl);
  });
})();
