(() => {
  const templates=JSON.parse(document.querySelector('#mail-template-data')?.textContent || '{}');
  const templateForm=document.querySelector('#template-form');
  const drafts={};let currentEvent=templateForm?.elements.event.value;
  const remember=()=>{if(templateForm&&currentEvent)drafts[currentEvent]={subject:templateForm.elements.subject.value,body:templateForm.elements.body.value};};
  const fillTemplate=()=>{const t=drafts[templateForm.elements.event.value]||templates[templateForm.elements.event.value];if(t){templateForm.elements.subject.value=t.subject;templateForm.elements.body.value=t.body;}};
  templateForm?.elements.event.addEventListener('change',()=>{remember();currentEvent=templateForm.elements.event.value;fillTemplate();templateForm.querySelector('[data-mail-feedback]').textContent=drafts[currentEvent]?'已恢复此模板的本页草稿；尚未保存的内容只保留在当前页面。':'';});
  if(templateForm)fillTemplate();
  window.addEventListener('beforeunload',event=>{if(!templateForm)return;remember();if(Object.keys(drafts).some(key=>JSON.stringify(drafts[key])!==JSON.stringify(templates[key]))){event.preventDefault();event.returnValue='';}});
  async function act(form,url,button){
    if(form.dataset.mailBusy)return;
    form.dataset.mailBusy='true';form.setAttribute('aria-busy','true');
    const feedback=form.querySelector('[data-mail-feedback]');
    const controls=[...form.querySelectorAll('button'),...document.querySelectorAll(`[data-mail-form="${form.id}"]`)];
    const enabled=controls.filter(b=>!b.disabled);enabled.forEach(b=>b.disabled=true);
    feedback.textContent='正在处理…';
    const data=Object.fromEntries(new FormData(form));
    if(form.querySelector('[name=events]'))data.events=[...form.querySelectorAll('[name=events]:checked')].map(i=>i.value);
    if(form.elements.quiet_enabled)data.quiet_enabled=form.elements.quiet_enabled.checked;
    if(form.elements.enabled)data.enabled=form.elements.enabled.checked;
    if(form.id==='smtp-form'){data.events=[...form.querySelectorAll('[name=events]:checked')].map(i=>i.value);data.recipient=document.querySelector('#test-recipient').value;}
    try{
      const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if(response.redirected)throw Error('登录已过期，请刷新页面后重试。');
      const result=await response.json();if(!response.ok)throw Error(result.message || '操作未完成');
      feedback.textContent=result.message || '预览已更新';
      if(result.html){const frame=document.querySelector('#mail-preview');frame.srcdoc=result.html;frame.hidden=false;}
      if(result.template){templates[data.event]={subject:result.template.subject,body:result.template.body};delete drafts[data.event];if(currentEvent===data.event)fillTemplate();}
      if(url.endsWith('/template')){templates[data.event]={subject:data.subject,body:data.body};delete drafts[data.event];}
      if(url==='/notifications/verify'||url==='/notifications/save')window.location.reload();
    }catch(error){feedback.textContent=error.message || '网络连接失败。请核对发送记录后再试，避免重复发送。';}
    finally{delete form.dataset.mailBusy;form.removeAttribute('aria-busy');enabled.forEach(b=>b.disabled=false);}
  }
  document.querySelectorAll('[data-json-form]').forEach(form=>form.addEventListener('submit',event=>{event.preventDefault();event.stopPropagation();act(form,form.dataset.jsonForm,event.submitter);}));
  document.querySelectorAll('[data-mail-action]').forEach(button=>button.addEventListener('click',()=>{
    const form=document.getElementById(button.dataset.mailForm);
    if(button.dataset.mailAction==='restore' && !window.confirm('恢复此类通知的默认模板？'))return;
    const prefix=form.id==='verify-form'?'/notifications/':'/admin/settings/mail/';
    act(form,prefix+button.dataset.mailAction,button);
  }));
})();
