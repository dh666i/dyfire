import {createUI} from './components.js';

// One canonical implementation for account, journal and overview workflows.
export function installPages({state,studio}) {
  const view=new URLSearchParams(location.search).get('view');
  const canonical={accounts:'/accounts',runs:'/runs',overview:'/dashboard'};
  if(canonical[view]){location.replace(canonical[view]);return;}
  if(view!=='updates')return;
  const {esc}=studio,{pageHead,empty,section}=createUI(esc);
  const main=document.querySelector('main');[...main.children].forEach(child=>child.hidden=true);
  const page=document.createElement('div');page.className='site-page';main.append(page);
  document.querySelectorAll('header nav a').forEach(a=>a.classList.remove('active'));
  if(state.user.role!=='admin'){page.innerHTML=empty('页面不存在');return;}
  page.innerHTML=pageHead(['SYSTEM RELEASES','版本更新','检查版本、安装更新和查看更新状态。'])+section('01 / DYFIRE','应用版本',`<div data-system-updates data-csrf="${esc(state.csrf)}"><div class="setting-row"><h3>当前版本</h3><strong data-current-version>正在读取</strong></div><p role="status">正在连接更新服务</p><div class="form-actions"><button class="primary-action" type="button" data-update-check>检查更新</button><button class="primary-action" type="button" data-update-install disabled>安装更新</button></div></div>`);
  const script=document.createElement('script');script.src='/static/system_updates.js';document.body.append(script);
}
