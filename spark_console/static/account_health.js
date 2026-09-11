(() => {
  'use strict';
  const rows = [...document.querySelectorAll('[data-health-account]')];
  if (!rows.length) return;
  const csrf = document.querySelector('[data-csrf-token]')?.dataset.csrfToken;
  let stopped = false, polling = false;
  function paint(row, health) {
    row.dataset.health = health.state;
    const label = row.querySelector('[data-health-label]');
    label.textContent = `健康状态：${health.label}`;
    label.className = `account-health account-health-${health.state}`;
    row.querySelector('[data-health-reason]').textContent = health.reason || '';
    row.querySelector('[data-health-progress]').textContent = health.checking
      ? `正在进行第 ${health.attempts} 次登录复核…`
      : health.next_check_at
      ? `已安排第 ${health.attempts + 1} 次复核，预计 ${new Date(health.next_check_at).toLocaleTimeString('zh-CN')}`
      : health.state === 'temporary' ? '自动复核已结束，仍无法确认；请稍后复核或重新登录。'
      : health.state === 'verification' ? '请完成安全验证，系统不会自动尝试密码或验证码。'
      : health.state === 'relogin' ? '正在重新登录，其他操作等待登录完成。' : '';
    const button = row.querySelector('[data-health-check]');
    button.disabled = ['verification','relogin'].includes(health.state) || health.checking || Boolean(health.next_check_at) || row.dataset.checkBusy === 'true';
    const login = row.querySelector('[data-scan-account-id]');
    login.textContent = health.state === 'verification' ? '完成验证' : '重新登录';
  }
  async function request(url, options = {}) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 10000);
    try {
      const response = await fetch(url, {credentials:'same-origin', cache:'no-store', ...options, signal:abort.signal});
      if (response.redirected) { stopped = true; throw new Error('登录已过期，请重新登录网站'); }
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求未完成');
      return body;
    } finally { clearTimeout(timeout); }
  }
  async function refresh() {
    if (stopped || polling || document.hidden) return;
    polling = true;
    try {
      const data = await request('/accounts/health-state');
      for (const row of rows) if (data[row.dataset.healthAccount]) paint(row, data[row.dataset.healthAccount]);
    } catch (_) { /* Preserve the last known state during transient network errors. */ }
    finally { polling = false; }
  }
  rows.forEach(row => {
    row.querySelector('[data-health-check]').addEventListener('click', async event => {
      if (row.dataset.checkBusy === 'true') return;
      row.dataset.checkBusy = 'true'; event.currentTarget.disabled = true;
      const message = row.querySelector('[data-health-feedback]');
      message.textContent = '正在安排复核…';
      try {
        const data = await request(`/accounts/${encodeURIComponent(row.dataset.healthAccount)}/health-check`, {
          method:'POST', body:new URLSearchParams({csrf_token:csrf || ''})
        });
        message.textContent = data.message;
      } catch (error) { message.textContent = error.name === 'AbortError' ? '请求超时，请稍后刷新状态' : error.message; }
      finally { row.dataset.checkBusy = 'false'; row.querySelector('[data-health-check]').disabled = false; await refresh(); }
    });
  });
  let timer = setInterval(refresh, 10000);
  window.addEventListener('pagehide', () => { stopped = true; clearInterval(timer); });
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;clearInterval(timer);timer=setInterval(refresh,10000);refresh();}});
  document.addEventListener('visibilitychange', refresh);
  refresh();
})();
