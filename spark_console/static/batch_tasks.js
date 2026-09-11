(() => {
  const toolbar=document.querySelector('[data-batch-toolbar]');if(!toolbar)return;
  const choices=[...document.querySelectorAll('[data-batch-task]')];if(!choices.length)return;
  toolbar.hidden=false;
  const selected=()=>choices.filter(c=>c.checked && !c.closest('[data-task-id]').hidden);
  const all=toolbar.querySelector('[data-batch-all]');
  const count=()=>{toolbar.dataset.selection=selected().length?'active':'empty';const visible=choices.filter(c=>!c.closest('[data-task-id]').hidden),n=selected().length;toolbar.querySelector('[data-batch-count]').textContent=`当前结果已选 ${n} 个`;all.checked=visible.length>0&&n===visible.length;all.indeterminate=n>0&&n<visible.length;};
  document.addEventListener('task-filter-updated',()=>{choices.forEach(c=>{if(c.closest('[data-task-id]').hidden)c.checked=false;});count();});
  const clear=document.createElement('button');clear.type='button';clear.className='quiet';clear.textContent='取消选择';clear.dataset.batchClear='';clear.addEventListener('click',()=>{choices.forEach(c=>c.checked=false);count();});toolbar.querySelector('[data-batch-count]').after(clear);
  choices.forEach(c=>c.addEventListener('change',count));count();
  toolbar.querySelector('[data-batch-all]').addEventListener('change',event=>{choices.forEach(c=>{if(!c.closest('[data-task-id]').hidden)c.checked=event.target.checked;});count();});
  const action=toolbar.querySelector('[data-batch-action]'),time=toolbar.querySelector('[data-batch-time]'),button=toolbar.querySelector('[data-batch-submit]'),result=toolbar.querySelector('[data-batch-result]');
  action.addEventListener('change',()=>time.hidden=action.value!=='time');
  document.querySelector('[data-task-filter-toolbar]')?.addEventListener('input',()=>setTimeout(count,0));
  document.querySelector('[data-task-filter-toolbar]')?.addEventListener('click',()=>setTimeout(count,0));
  button.addEventListener('click',async()=>{
    const targets=selected();if(!targets.length || targets.length>100){result.textContent='请选择 1–100 个任务';return;}
    if(action.value==='time'&&!time.value){result.textContent='请选择新的发送时间';return;}
    const names=targets.slice(0,5).map(c=>c.getAttribute('aria-label').replace(/^选择 | 的任务$/g,'')).join('、');
    if(!confirm(`将对 ${targets.length} 个任务执行「${action.options[action.selectedIndex].text}」${action.value==='time'?'：'+time.value:''}。\n${names}${targets.length>5?'等':''}\n恢复后仅执行后续计划，不补发错过的消息。确认继续？`))return;
    button.disabled=true;result.textContent='正在处理，请勿重复提交…';
    try{
      const response=await fetch('/tasks/batch',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:toolbar.dataset.csrf,ids:targets.map(c=>c.value),action:action.value,time:time.value})});
      if(!response.ok||response.redirected)throw Error('结果无法确认，请刷新列表核对后再操作。');
      const payload=await response.json();result.replaceChildren();
      const summary=document.createElement('p');summary.textContent=`已完成 ${payload.results.filter(r=>r.ok).length} / ${payload.results.length} 个。刷新列表查看最新状态。`;result.append(summary);
      payload.results.filter(r=>!r.ok).forEach(r=>{const item=document.createElement('p');item.textContent=`${r.name}：${r.error}`;result.append(item);});
      const refresh=document.createElement('a');refresh.href='/tasks';refresh.textContent='刷新任务列表';result.append(refresh);
    }catch(error){result.textContent=error.message;}
    // A refresh is required to avoid operating on stale row state.
  });
})();
