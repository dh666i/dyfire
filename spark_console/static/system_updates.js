(() => {
  const root = document.querySelector('[data-system-updates]');
  if (!root) return;
  const status = root.querySelector('[role=status]');
  const check = root.querySelector('[data-update-check]');
  const install = root.querySelector('[data-update-install]');
  let latest = '', pending = false, timer;
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
      const newer = (a,b) => {const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i];}return false;};
      status.textContent = !data.configured ? '请先配置服务器更新服务' : !data.online ? '更新服务暂未连接' : data.message || '更新服务已连接';
      const busy = ['checking','pulling','building','stopping','installing','rollback'].includes(data.phase);
      check.disabled = pending || !data.online || busy;
      install.disabled = pending || !data.online || busy || !latest || !newer(latest, data.current);
      install.textContent = latest ? '更新到 ' + latest : '安装更新';
    } catch (error) {status.textContent=error.message; check.disabled=install.disabled=true;}
  }
  async function submit(action) {
    if(pending)return;
    if(action==='install'&&!window.confirm('将备份当前 Web 配置并更新网页服务，期间网页可能短暂不可用；抖音登录和发送服务继续运行。检查失败时回退旧镜像。继续更新到 '+latest+'？'))return;
    pending=true; check.disabled=install.disabled=true;
    try {await request('/api/admin/updates/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify({version:latest})});status.textContent='请求已提交，等待更新服务处理';}
    catch(error){status.textContent=error.message;}
    finally{pending=false;}
  }
  check.onclick=()=>submit('check'); install.onclick=()=>submit('install');
  refresh();timer=setInterval(()=>{if(!document.hidden&&!pending)refresh();},5000);
  window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
})();
