import {studio} from './main.js';
import './pages.css';
import {installPages} from './src/connected-pages.js';
const {esc,toast} = studio;
const $ = s => document.querySelector(s);
let state, contacts=[], busy=false, contactGeneration=0;
async function request(path, body) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',signal:controller.signal,
      ...(body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state?.csrf||''},body:JSON.stringify(body)}:{})});
    if(response.status===401){location.assign('/login');throw new Error('请登录后继续');}
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'操作未完成，请检查输入');
    return data;
  } catch(error) {
    if(error.name==='AbortError')throw new Error('请求超时，请刷新核对结果后再决定是否重试。');
    throw error;
  } finally{clearTimeout(timer);}
}
function decorateTasks(){
  for(const task of studio.tasks){
    const button=document.querySelector(`[data-toggle="${CSS.escape(task.id)}"]`);
    if(!button)continue;
    const row=button.closest('.task-row');
    const avatar=row.querySelector('.avatar');
    if(task.avatar){const image=document.createElement('img');image.src=task.avatar;image.alt='';image.loading='lazy';image.referrerPolicy='no-referrer';image.style.cssText='width:100%;height:100%;object-fit:cover;border-radius:inherit';image.onerror=()=>image.remove();avatar.prepend(image);}
    const controls=document.createElement('div');controls.style.cssText='display:flex;align-items:center;gap:10px;flex-wrap:wrap';button.replaceWith(controls);controls.append(button);
    const edit=document.createElement('a');edit.href='/tasks/'+encodeURIComponent(task.id)+'/edit';edit.textContent='编辑';edit.className='small-action';controls.append(edit);
  }
}
const originalRender=studio.render;
async function load(){
  const account=$('#account').value;
  state=await request('/api/studio');studio.tasks=state.tasks;
  $('#account').innerHTML=state.accounts.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.health.label)}</option>`).join('')||'<option value="">请先绑定抖音账号</option>';
  if(state.accounts.some(a=>a.id===account))$('#account').value=account;
  $('#account-status').textContent=state.accounts.length?`${state.accounts.length} 个账号 · ${state.accounts.filter(a=>!a.health.blocked).length} 个可用`:'尚未绑定账号';
  originalRender();
  $('#create-form .submit').disabled=!state.subscription.can_add_task;
  $('#form-feedback').textContent=state.subscription.can_add_task?'':state.accounts.length?'当前不可创建任务，请检查会员状态和任务额度。':'请先绑定抖音账号。';
  const profile=$('.profile');profile.textContent=state.user.name.slice(0,1);profile.setAttribute('aria-label','账户中心');
  $('#session-logout')?.remove();
  const logout=document.createElement('form');logout.id='session-logout';logout.method='POST';logout.action='/logout';
  logout.innerHTML=`<input type="hidden" name="csrf_token" value="${esc(state.csrf)}"><button type="submit" class="small-action">退出登录</button>`;
  $('.profile-popover').append(logout);
  $('#admin-link')?.remove();if(state.user.role==='admin')$('.profile-popover').insertAdjacentHTML('beforeend','<div id="admin-link"><a href="/admin/overview">管理后台 ↗</a><a href="/studio?view=updates">版本更新 ↗</a></div>');
}
$('header nav').innerHTML='<a class="active" href="/studio">续火任务 <sup>01</sup></a><a href="/accounts">抖音账号 <sup>02</sup></a><a href="/runs">执行记录 <sup>03</sup></a>';
$('header .profile').outerHTML='<details class="profile-menu"><summary class="profile" aria-label="账户中心">·</summary><div class="profile-popover"><a href="/studio?view=overview">今日概览</a><a href="/subscription">会员订阅</a><a href="/account-center">账户中心</a><a href="/notifications">通知设置</a><a href="/help">使用帮助</a></div></details>';
document.addEventListener('click',e=>{if(!e.target.closest('.profile-menu'))$('.profile-menu').open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('.profile-menu').open=false;});
async function loadContacts(){
  const generation=++contactGeneration;
  contacts=[];studio.friends.splice(0);studio.syncSelection();
  const account=$('#account').value;
  $('#friend-list').textContent=account?'正在读取好友列表…':'请先绑定抖音账号';
  if(!account)return;
  try{
    const data=await request('/accounts/'+encodeURIComponent(account)+'/conversations');
    if(generation!==contactGeneration)return;
    contacts=data.items;studio.friends.push(...contacts.map(c=>c.name));
    $('#friend-list').innerHTML=contacts.map((c,i)=>`<label class="friend-option" data-name="${esc([c.name,c.nickname,c.remark_name].filter(Boolean).join(' ').toLowerCase())}"><input type="checkbox" value="${i}" name="friend"><span class="avatar">${c.avatar_url?`<img src="${esc(c.avatar_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">`:esc(c.name[0])}</span><span>${esc(c.name)}<small>${esc(c.nickname||'抖音好友')}</small></span><span class="checkmark">✓</span></label>`).join('')||'暂无好友记录，请在账号页同步好友后刷新。';
    $('#friend-search').value='';
  }catch(error){if(generation===contactGeneration)$('#friend-list').textContent=error.message;}
}
$('#friend-list').addEventListener('change',studio.syncSelection);
$('#account').addEventListener('change',loadContacts);
$('#choose-friends').onclick=async()=>{$('#friends-dialog').showModal();if(!contacts.length)await loadContacts();};
$('#create-form').onsubmit=async e=>{
  e.preventDefault();if(busy||!state)return;
  const selected=[...document.querySelectorAll('[name=friend]:checked')].map(c=>contacts[Number(c.value)]);
  if(!selected.length){$('#form-feedback').textContent='请至少选择一位好友';return;}
  const text=$('#kind').value==='文字消息';
  if(text&&!$('#message').value.trim()){$('#form-feedback').textContent='请填写发送内容';return;}
  busy=true;$('#create-form .submit').disabled=true;
  try{
    await request('/api/studio/tasks',{account_id:$('#account').value,targets:selected.map(c=>({name:c.name,sec_uid:c.sec_uid||''})),send_time:$('#time').value,message_kind:text?'text':'douyin_sticker',message_template:text?$('#message').value.trim():'续火花'});
    $('#clear-selection').click();await load();toast('续火任务已创建');
  }catch(error){$('#form-feedback').textContent=error.message;}
  finally{busy=false;$('#create-form .submit').disabled=!state.subscription.can_add_task;}
};
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-toggle]');if(!button||busy)return;
  const task=studio.tasks.find(t=>t.id===button.dataset.toggle);if(!task)return;
  busy=true;button.disabled=true;
  try{await request('/api/studio/tasks/'+encodeURIComponent(task.id)+'/enabled',{enabled:!task.enabled});await load();toast(task.enabled?'任务已暂停':'任务已恢复');}
  catch(error){toast(error.message);}
  finally{busy=false;button.disabled=false;}
});
load().then(()=>installPages({state,studio,request})).catch(error=>{$('#account-status').textContent='暂时无法读取';$('#form-feedback').textContent=error.message;$('#create-form .submit').disabled=true;});
document.addEventListener('studio:render-tasks',decorateTasks);
