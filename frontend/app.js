import {studio} from './main.js';
import './pages.css';
import '../spark_console/static/ui_select.css';
import '../spark_console/static/ui_select.js';
import '../spark_console/static/interaction_polish.css';
import '../spark_console/static/motion.css';
import '../spark_console/static/motion.js';
import '../spark_console/static/task_experience.js';
import '../spark_console/static/studio_tokens.css';
import '../spark_console/static/design_system.css';
import '../spark_console/static/studio_components.css';
import '../spark_console/static/design_system.js';
import '../spark_console/static/mobile_workspace.js';
import {installPages} from './src/connected-pages.js';
import {installTaskWorkspace} from './src/task-workspace.js';
const {esc,toast} = studio;
window.sparkToast=toast;
const $ = s => document.querySelector(s);
$('.filters [data-filter="on"]').textContent='已启用';
const activeLabel=$('#active-count')?.parentElement.firstChild;if(activeLabel?.nodeType===3)activeLabel.textContent='已启用 ';
let state, contacts=[], busy=false, contactGeneration=0, contactsAccount='';
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
    avatar.textContent=task.name.slice(0,1);
    if(task.avatar){const image=document.createElement('img');image.src=task.avatar;image.alt='';image.loading='lazy';image.referrerPolicy='no-referrer';image.style.cssText='width:100%;height:100%;object-fit:cover;border-radius:inherit';image.onerror=()=>image.remove();avatar.prepend(image);}
    const controls=document.createElement('div');controls.className='task-row-controls';controls.style.cssText='display:flex;align-items:center;gap:10px;flex-wrap:wrap';button.replaceWith(controls);controls.append(button);
    const edit=document.createElement('a');edit.href='/tasks/'+encodeURIComponent(task.id)+'/edit';edit.textContent='编辑';edit.className='small-action';controls.append(edit);
  }
}
const originalRender=studio.render;
async function load(){
  const account=$('#account').value || new URLSearchParams(location.search).get('account');
  state=await request('/api/studio');studio.tasks=state.tasks;
  $('#create-form').dataset.csrf=state.csrf;
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
  $('#admin-link')?.remove();if(state.user.role==='admin')logout.insertAdjacentHTML('beforebegin','<div id="admin-link"><a href="/admin/overview">管理后台 ↗</a><a href="/studio?view=updates">版本更新 ↗</a></div>');
  $('.menu-identity')?.remove();$('.profile-popover').insertAdjacentHTML('afterbegin',`<div class="menu-identity"><strong>${esc(state.user.name)}</strong><small>${state.user.role==='admin'?'管理员':'个人工作台'}</small></div>`);
  refreshSchedule();
}
$('header nav').innerHTML='<a class="active" href="/studio">续火任务</a><a href="/accounts">抖音账号</a><a href="/runs">执行记录</a>';
$('header .profile').outerHTML='<details class="profile-menu"><summary class="profile" aria-label="账户中心">·</summary><div class="profile-popover"><a href="/studio?view=overview">今日概览</a><a href="/subscription">会员订阅</a><a href="/account-center">账户中心</a><a href="/notifications">通知设置</a><a href="/help">使用帮助</a></div></details>';
document.addEventListener('click',e=>{if(!e.target.closest('.profile-menu'))$('.profile-menu').open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('.profile-menu').open=false;});
function existingTask(c){return studio.tasks.find(t=>t.accountId===$('#account').value&&(t.sec_uid&&c.sec_uid?t.sec_uid===c.sec_uid:t.name.trim().toLocaleLowerCase()===c.name.trim().toLocaleLowerCase()));}
async function loadContacts(searchItems=null){
  const generation=++contactGeneration;
  const sameAccount=contactsAccount===$('#account').value;
  const previousMarkup=$('#friend-list').innerHTML,previousContacts=contacts;
  const chosen=sameAccount ? [...document.querySelectorAll('[name=friend]:checked')].map(el=>contacts[Number(el.value)]).filter(Boolean) : [];
  const identity=c=>c.sec_uid||c.name;
  const chosenIds=new Set(chosen.map(identity));
  contactsAccount=$('#account').value;
  if(!sameAccount){contacts=[];studio.friends.splice(0);studio.syncSelection();}
  const account=$('#account').value;
  if(!sameAccount||!contacts.length)$('#friend-list').textContent=account?'正在读取好友列表…':'请先绑定抖音账号';
  $('#friend-list').setAttribute('aria-busy',String(!!account));
  if(!account)return;
  try{
    const data=searchItems ? {items:searchItems} : await request('/accounts/'+encodeURIComponent(account)+'/conversations');
    if(generation!==contactGeneration)return;
    contacts=[...data.items,...chosen.filter(c=>!data.items.some(item=>identity(item)===identity(c)))];studio.friends.splice(0,studio.friends.length,...contacts.map(c=>c.name));
    $('#friend-list').innerHTML=contacts.map((c,i)=>`<label class="friend-option" data-name="${esc([c.name,c.nickname,c.remark_name].filter(Boolean).join(' ').toLowerCase())}"><input type="checkbox" value="${i}" name="friend" ${existingTask(c)?'disabled aria-label="已有任务"':''}><span class="avatar">${c.avatar_url?`<img src="${esc(c.avatar_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">`:esc(c.name[0])}</span><span>${esc(c.name)}<small>${esc(existingTask(c)?(existingTask(c).enabled?'已有任务':'已有任务 · 已暂停'):c.nickname||'抖音好友')}</small></span><span class="checkmark">✓</span></label>`).join('')||'暂无好友记录，请在账号页同步好友后刷新。';
    if(!searchItems)$('#friend-search').value='';
    $('#friend-empty').hidden=contacts.length>0;
    refreshSchedule();
    document.querySelectorAll('[name=friend]').forEach(el=>{el.checked=!el.disabled&&chosenIds.has(identity(contacts[Number(el.value)]));});studio.syncSelection();
  }catch(error){if(generation===contactGeneration){if(sameAccount){contacts=previousContacts;$('#friend-list').innerHTML=previousMarkup;document.querySelectorAll('[name=friend]').forEach(el=>el.checked=chosenIds.has(identity(contacts[Number(el.value)])));studio.syncSelection();toast('读取未完成，保留上次列表和选择。');}else $('#friend-list').textContent=error.message;}}
  finally{if(generation===contactGeneration)$('#friend-list').setAttribute('aria-busy','false');}
}
$('#friend-list').addEventListener('change',()=>{studio.syncSelection();refreshSchedule();});
let timeTouched=false, scheduleTimer=0, idleApplied=false;
function selectedFriends(){return [...document.querySelectorAll('[name=friend]:checked')].map(c=>contacts[Number(c.value)]).filter(Boolean);}
function refreshSchedule(){
  clearTimeout(scheduleTimer);
  scheduleTimer=setTimeout(async()=>{
    const account=$('#account').value, preferred=$('#time').value||'09:30', count=Math.max(1,selectedFriends().length);
    const hint=$('#schedule-hint'), box=$('#schedule-suggestions');
    if(!hint)return;
    try{
      const data=await request(`/api/studio/schedule?account_id=${encodeURIComponent(account)}&count=${count}&preferred=${encodeURIComponent(preferred)}`);
      const times=data.times||[];
      if(!idleApplied && !timeTouched && data.suggested?.[0] && data.load>(data.suggested[0].load||0)){
        idleApplied=true;$('#time').value=data.suggested[0].start;refreshSchedule();return;
      }
      idleApplied=true;
      if(times.length>1) hint.textContent=`将按 ${times[0]} 至 ${times[times.length-1]} 错开，每位好友间隔 ${data.gap_minutes||1} 分钟。`;
      else hint.textContent='多位好友会自动错开 5 分钟，并优先安排到全站较空闲的时段。';
      const chips=(data.suggested||[]).slice(0,3);
      box.hidden=!chips.length;
      box.innerHTML=chips.map(item=>`<button type="button" class="schedule-chip" data-start="${esc(item.start)}">${esc(item.start)}${item.times.length>1?` 起` : ''}<small>全站较空闲</small></button>`).join('');
    }catch{/* keep last hint */}
  },200);
}
$('#time').addEventListener('input',()=>{timeTouched=true;refreshSchedule();});
$('#schedule-suggestions')?.addEventListener('click',e=>{
  const chip=e.target.closest('[data-start]');if(!chip)return;
  timeTouched=true;$('#time').value=chip.dataset.start;refreshSchedule();
});
$('#select-visible')?.addEventListener('click',refreshSchedule);
$('#clear-selection')?.addEventListener('click',refreshSchedule);
$('#account').addEventListener('change',()=>loadContacts());
document.addEventListener('directory-updated',e=>{if(e.detail.accountId===$('#account').value)loadContacts(e.detail.query ? e.detail.items : null);});
$('#choose-friends').onclick=async()=>{$('#friends-dialog').showModal();await load();await loadContacts();};
$('#create-form').onsubmit=async e=>{
  e.preventDefault();if(busy||!state)return;
  const selected=selectedFriends();
  if(!selected.length){$('#form-feedback').textContent='请至少选择一位好友';return;}
  const text=$('#kind').value==='文字消息';
  const snapshot=JSON.stringify([$('#account').value,$('#time').value,$('#kind').value,$('#message').value,selected.map(c=>c.sec_uid||c.name)]);
  if(text&&!$('#message').value.trim()){$('#form-feedback').textContent='请填写发送内容';return;}
  busy=true;$('#create-form .submit').disabled=true;
  const submit=$('#create-form .submit'),submitLabel=submit.innerHTML;
  submit.textContent='正在创建…';submit.setAttribute('aria-busy','true');
  try{
    const preview=await request('/tasks/preview',{csrf_token:state.csrf,account_id:$('#account').value,time:$('#time').value,targets:selected.map(c=>({name:c.name,sec_uid:c.sec_uid||''}))});
    if(preview.conflicts.length)throw Error('同一时间已有任务：'+preview.conflicts.join('、'));
    if(preview.blocked)throw Error('请先完成账号复核或验证');
    const times=preview.times||[preview.time];
    const timeLine=times.length>1?`每天 ${times[0]}–${times[times.length-1]}（北京时间，${preview.count} 位好友各错开 ${preview.gap_minutes||1} 分钟）`:`每天 ${preview.time}（北京时间） · ${preview.count} 位好友`;
    if(!await window.sparkReview('确认每日计划',[`账号：${preview.account}`,timeLine,selected.map(c=>c.name).join('、'),text?$('#message').value:'续火花表情','从下一次计划时间开始执行，不补发历史消息。']))return;
    const nowSelected=selectedFriends();
    if(snapshot!==JSON.stringify([$('#account').value,$('#time').value,$('#kind').value,$('#message').value,nowSelected.map(c=>c.sec_uid||c.name)]))throw Error('表单内容已变化，请重新确认');
    const created=await request('/api/studio/tasks',{account_id:$('#account').value,targets:selected.map(c=>({name:c.name,sec_uid:c.sec_uid||''})),send_time:$('#time').value,message_kind:text?'text':'douyin_sticker',message_template:text?$('#message').value.trim():'续火花'});
    $('#clear-selection').click();document.dispatchEvent(new Event('task-created'));await load();document.dispatchEvent(new CustomEvent('studio:tasks-created',{detail:{ids:created.ids,account:$('#account').value}}));
    const range=created.times?.length>1?`，${created.times[0]}–${created.times[created.times.length-1]}`:'';
    toast(`已创建 ${created.ids.length} 个续火任务${range}`);
    document.querySelector('#workspace')?.scrollIntoView({block:'start'});
    created.ids.forEach(id=>document.querySelector(`[data-toggle="${CSS.escape(id)}"]`)?.closest('.task-row')?.classList.add('task-just-created'));
  }catch(error){$('#form-feedback').textContent=error.message;}
  finally{busy=false;submit.innerHTML=submitLabel;submit.removeAttribute('aria-busy');submit.disabled=!state.subscription.can_add_task;}
};
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-toggle]');if(!button||busy)return;
  const task=studio.tasks.find(t=>t.id===button.dataset.toggle);if(!task)return;
  busy=true;button.disabled=true;const label=button.textContent;button.textContent='保存中…';button.setAttribute('aria-busy','true');
  try{
    const enabled=!task.enabled;
    const changed=await request('/tasks/batch',{csrf_token:state.csrf,ids:[task.id],action:enabled?'enable':'pause'});
    if(!changed.results[0].ok)throw Error(changed.results[0].error);
    task.enabled=enabled;
    // Preserve the other rows, images, and focus; reconcile filters only when needed.
    const row=button.closest('.task-row'),selectedFilter=document.querySelector('[data-filter].selected')?.dataset.filter;
    if(selectedFilter==='on'||selectedFilter==='off') originalRender();
    else {
      button.textContent=enabled?'暂停':'恢复';
      button.setAttribute('aria-label',`${enabled?'暂停':'恢复'} ${task.name} 的任务`);
      const status=row.querySelector('.state');
      status.className='state '+(!enabled?'paused':task.confirmed?'confirmed':'waiting');
      status.textContent=!enabled?'已暂停':task.confirmed?'发送已确认':task.status==='submitted_unconfirmed'?'已提交，待核实':task.status==='failed'?'执行失败':task.status==='running'?'执行中':task.status==='retrying'?'等待恢复重试':'等待执行';
      status.prepend(document.createElement('i'));
      $('#active-count').textContent=String(studio.tasks.filter(t=>t.enabled).length).padStart(2,'0');
    }
    window.sparkEmphasize?.(row);toast(enabled?'任务已恢复':'任务已暂停');
  }
  catch(error){button.textContent=label;toast(error.message);}
  finally{busy=false;button.disabled=false;button.removeAttribute('aria-busy');}
});
load().then(()=>{
  const notices={'task-updated':'任务已保存','task-created':'任务已创建','task-deleted':'任务已删除','task-enabled':'任务已恢复','task-paused':'任务已暂停','task-running':'任务正在执行，请稍后操作'};
  const url=new URL(location.href),notice=notices[url.searchParams.get('notice')];
  if(notice){toast(notice);url.searchParams.delete('notice');history.replaceState(history.state,'',url.pathname+url.search+url.hash);}
  document.querySelectorAll('a[href="/studio?view=overview"]').forEach(link=>link.href='/dashboard');
  $('.profile-popover a[href="/help"]').insertAdjacentHTML('beforebegin','<a href="/attention">待处理事项</a>');
  const directory=document.createElement('script');directory.src='/static/directory_ui.js?v=0.1.5';document.body.append(directory);
  installPages({state,studio,request});
  installTaskWorkspace({state,studio});
  if(location.hash==='#new-task')document.querySelector('[data-open-studio-create]')?.click();
}).catch(error=>{$('#account-status').textContent='暂时无法读取';$('#form-feedback').textContent=error.message;$('#create-form .submit').disabled=true;});
document.addEventListener('studio:render-tasks',decorateTasks);
document.querySelector('#friend-search').addEventListener('input',event=>{
  const query=event.target.value.trim().toLocaleLowerCase();let count=0;
  document.querySelectorAll('.friend-option').forEach((row,i)=>{const c=contacts[i];const match=c&&[c.name,c.nickname,c.remark_name,c.unique_id,c.short_id].some(v=>String(v||'').toLocaleLowerCase().includes(query));row.hidden=!match;if(match)count++;});
  $('#friend-empty').hidden=count>0;$('#friend-empty').textContent='没有匹配的已同步会话，可以同步更多最近会话后再试。';
});
