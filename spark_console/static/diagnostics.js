(() => {
  const host=document.querySelector('[data-diagnostics]');if(!host)return;
  const status=host.querySelector('[role=status]'),list=host.querySelector('[data-diagnostic-list]'),button=host.querySelector('button');
  async function load(){button.disabled=true;status.textContent='正在读取诊断…';
    try{const r=await fetch('/admin/diagnostics',{cache:'no-store',credentials:'same-origin'});if(!r.ok||r.redirected)throw Error('无法读取诊断，请检查登录状态');const data=await r.json();
      const schedule=data.backup.schedule_enabled===false?'自动备份已停用':data.backup.schedule_enabled===true?'自动备份已启用':'自动备份计划状态未提供';
      const at=data.backup.at?new Date(data.backup.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'时间未记录';
      status.textContent=schedule+' · '+(data.backup.status==='success'?`最近历史备份：${at}（北京时间），完整性检查通过`:(data.backup.message||'暂无备份记录'));
      const fragment=document.createDocumentFragment();
      const labels={success:'已发出',submitted_unconfirmed:'待核实',failed:'未发出',running:'执行中',retrying:'等待重试',skipped:'已跳过',queued:'等待执行'};
      for(const item of data.items){const row=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('p');summary.textContent=`${item.name} · ${labels[item.status]||item.status}`;
        const sec=v=>v===null?'未记录':v+' 秒';body.textContent=`排期至启动：${sec(item.queue)}；启动至提交：${sec(item.before_submit)}；提交至结束：${sec(item.confirmation)}；执行总耗时：${sec(item.total)}。编号：${item.id}`;row.append(summary,body);fragment.append(row);}
      list.replaceChildren(fragment);if(!data.items.length)list.textContent='暂无执行样本。';
    }catch(error){status.textContent=error.message;}finally{button.disabled=false;}}
  button.onclick=load;load();
})();
