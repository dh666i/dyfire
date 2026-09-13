(() => {
  'use strict';
  window.sparkTaskRequest=async(url,data)=>{
    const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(r.redirected)throw Error('登录状态已变化，请重新登录');const body=await r.json();
    if(!r.ok)throw Error(typeof body.detail==='string'?body.detail:'操作未完成');return body;
  };
  window.sparkReview=(title,lines)=>new Promise(resolve=>{
    const drawer=document.querySelector('.ui-create-drawer[open]');
    const d=document.createElement(drawer?'section':'dialog'),h=document.createElement('h2'),content=document.createElement('div'),footer=document.createElement('footer');
    d.className='task-review';h.textContent=title;h.id='task-review-title';d.setAttribute('aria-labelledby',h.id);content.className='task-review-content';footer.className='form-actions';
    lines.forEach(line=>{const p=document.createElement('p');p.textContent=line;content.append(p);});
    const cancel=document.createElement('button'),accept=document.createElement('button'),origin=document.activeElement;
    cancel.type=accept.type='button';cancel.textContent='返回修改';accept.textContent='确认执行';let done=false;
    const composer=drawer?.querySelector('.composer,.task-create-panel');
    const escape=event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish(false);}};
    function finish(value){if(done)return;done=true;if(!drawer)d.close();d.remove();if(composer)composer.hidden=false;document.removeEventListener('keydown',escape,true);origin?.focus();resolve(value);}
    cancel.onclick=()=>finish(false);accept.onclick=()=>finish(true);d.addEventListener('cancel',e=>{e.preventDefault();finish(false);});d.addEventListener('click',e=>{if(e.target===d)finish(false);});
    footer.append(cancel,accept);d.append(h,content,footer);(drawer||document.body).append(d);
    if(drawer){if(composer)composer.hidden=true;d.setAttribute('role','region');document.addEventListener('keydown',escape,true);}else d.showModal();cancel.focus();
  });
  window.sparkApplyTasks=results=>{
    for(const item of results){if(!item.ok)continue;const row=document.querySelector(`[data-task-id="${CSS.escape(item.id)}"]`);if(!row)continue;
      row.dataset.taskEnabled=String(item.enabled);row.classList.toggle('is-paused',!item.enabled);
      const state=row.querySelector('.task-overview-state>.pill');if(state){state.textContent=item.enabled?'已启用':'已暂停';state.className='pill '+(item.enabled?'good':'off');}
      const time=row.querySelector('.task-overview-time time');if(time)time.textContent=item.time;
      const next=row.querySelector('.task-overview-next>span:last-child');if(next)next.textContent=!item.enabled?'任务已暂停':item.next_run_at?new Date(item.next_run_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'等待调度';
      const button=row.querySelector('form[action$="/toggle"] button');if(button)button.textContent=item.enabled?'暂停':'启用';row.querySelector('details[open]')?.removeAttribute('open');window.sparkReveal?.(state);
      window.sparkEmphasize?.(row);
    }document.dispatchEvent(new Event('task-state-updated'));
  };
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(form.matches('[data-task-create-form]')){
      if(form.dataset.previewApproved==='true'){delete form.dataset.previewApproved;return;}
      event.preventDefault();if(form.dataset.previewBusy||form.dataset.submitting==='true')return;
      const button=event.submitter;form.dataset.previewBusy='true';if(button){button.disabled=true;button.classList.add('is-loading');}
      const data=new FormData(form),names=data.getAll('target_name'),uids=data.getAll('target_sec_uid');
      try{
        const p=await window.sparkTaskRequest('/tasks/preview',{csrf_token:data.get('csrf_token'),account_id:data.get('account_id'),time:data.get('send_time'),targets:names.map((name,i)=>({name,sec_uid:uids[i]}))});
        if(p.conflicts.length)throw Error('同一时间已有任务：'+p.conflicts.join('、'));if(p.blocked)throw Error('请先完成账号复核或验证');
        const yes=await window.sparkReview('确认每日计划',[`账号：${p.account}`,`每天 ${p.time}（北京时间） · ${p.count} 位好友`,names.join('、'),data.get('message_kind')==='douyin_sticker'?'续火花表情':'消息：'+data.get('message_template'),p.manual_count?`${p.manual_count} 位为手动输入，请核对准确昵称。`:'已选择同步会话中的好友。','从下一次计划时间开始，不补发历史消息。']);
        if(yes){if(JSON.stringify([...data])!==JSON.stringify([...new FormData(form)]))throw Error('表单内容已变化，请重新确认');form.dataset.previewApproved='true';if(button)button.disabled=false;form.requestSubmit(button);}
      }catch(error){window.sparkToast?.(error.message,'error');}
      finally{delete form.dataset.previewBusy;if(button&&form.dataset.submitting!=='true'){button.disabled=false;button.classList.remove('is-loading');}}return;
    }
    if(!form.matches('form[action$="/toggle"]')||!form.closest('[data-task-id]'))return;
    event.preventDefault();if(form.dataset.pending)return;form.dataset.pending='true';const button=form.querySelector('button'),row=form.closest('[data-task-id]');button.disabled=true;button.classList.add('is-loading');
    try{const p=await window.sparkTaskRequest('/tasks/batch',{csrf_token:new FormData(form).get('csrf_token'),ids:[row.dataset.taskId],action:row.dataset.taskEnabled==='true'?'pause':'enable'});window.sparkApplyTasks(p.results);const item=p.results[0];if(!item.ok)throw Error(item.error);window.sparkToast?.(item.enabled?'任务已恢复，将执行后续计划':'任务已暂停');}
    catch(error){window.sparkToast?.(error.message+'；可刷新列表核对状态。','error');}finally{delete form.dataset.pending;button.disabled=false;button.classList.remove('is-loading');}
  },true);
  window.addEventListener('load',()=>{if(location.hash.startsWith('#task-')){const row=document.querySelector(`[data-task-id="${CSS.escape(location.hash.slice(6))}"]`);if(row){row.hidden=false;row.scrollIntoView({block:'center'});row.classList.add('task-just-created');}}});
})();
