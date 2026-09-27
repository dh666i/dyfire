(() => {
  const root = document.querySelector('[data-system-updates]');
  if (!root) return;
  const status = root.querySelector('[role=status]');
  const check = root.querySelector('[data-update-check]');
  const install = root.querySelector('[data-update-install]');
  let latest = '', pending = false, timer;
  const busyPhases = new Set(['checking', 'pulling', 'web_pulling', 'building', 'stopping', 'installing', 'web_installing', 'rollback', 'rollback_pulling']);
  async function request(path, options) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '更新服务暂时不可用');
    return data;
  }
  async function refresh() {
    try {
      const data = await request('/api/admin/updates');
      root.querySelector('[data-current-version]').textContent = data.current;
      latest = data.latest || '';
      status.textContent = !data.configured ? '请先配置服务器更新服务' : !data.online ? '更新服务暂未连接' : data.message || '更新服务已连接';
      const busy = busyPhases.has(data.phase);
      check.disabled = pending || !data.online || busy;
      install.disabled = pending || !data.online || busy || !data.update_available;
      install.textContent = latest ? '更新到 ' + latest : '安装更新';
      const release = root.querySelector('[data-release-link]');
      if (release) { release.href = data.release_url || '#'; release.hidden = !data.release_url; }
    } catch (error) {status.textContent=error.message; check.disabled=install.disabled=true;}
  }
  async function submit(action) {
    if(pending)return;
    if(action==='install'&&!window.confirm('将备份当前数据并更新完整服务，期间网页和任务服务会短暂重启。检查失败时恢复旧镜像。继续更新到 '+latest+'？'))return;
    pending=true; check.disabled=install.disabled=true;
    try {const result=await request('/api/admin/updates/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify({version:action==='check'?'':latest})});status.textContent=result.operation_id?'请求已提交，等待更新服务处理（'+result.operation_id.slice(0,8)+'）':'请求已提交，等待更新服务处理';}
    catch(error){status.textContent=error.message;}
    finally{pending=false;}
  }
  check.onclick=()=>submit('check'); install.onclick=()=>submit('install');
  refresh();timer=setInterval(()=>{if(!document.hidden&&!pending)refresh();},5000);
  window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
})();
