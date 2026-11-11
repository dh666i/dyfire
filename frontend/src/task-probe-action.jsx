import React, {useEffect, useRef, useState} from 'react';
import {RowActionItem} from './row-actions.jsx';

export function createProbeAction({api}) {
function TaskProbeAction({taskId,runId,csrf,label='检查任务状态',menuItem=false}) {
  const [probe,setProbe]=useState(null); const [busy,setBusy]=useState(false); const [error,setError]=useState('');
  const generation=useRef(0);
  useEffect(()=>{
    const controller=new AbortController();const current=++generation.current;
    setProbe(null);setError('');setBusy(false);
    if(taskId)api(`/api/tasks/${taskId}/checks`,{signal:controller.signal}).then(value=>{
      const found=(value.items||[]).find(item=>runId?item.run_id===runId:item.kind==='selfcheck');
      if(current===generation.current&&found)setProbe(found);
    }).catch(value=>{if(value.name!=='AbortError'&&current===generation.current)setError(value.message)});
    return()=>{generation.current++;controller.abort()};
  },[taskId,runId]);
  useEffect(()=>{
    if(!probe?.id||!['queued','running'].includes(probe.status))return;
    const controller=new AbortController();let timer;
    const read=async()=>{
      if(controller.signal.aborted)return;
      if(document.visibilityState==='hidden'){timer=setTimeout(read,10000);return}
      try{const value=await api(probe.status_url,{signal:controller.signal});if(!controller.signal.aborted){setProbe(value);setError('')}}
      catch(value){if(value.name!=='AbortError')setError(value.message)}
      if(!controller.signal.aborted)timer=setTimeout(read,2500);
    };
    timer=setTimeout(read,500);
    return()=>{clearTimeout(timer);controller.abort()};
  },[probe?.id,probe?.status]);
  const start=async event=>{
    event?.preventDefault?.();event?.stopPropagation?.();if(busy||!taskId||['queued','running'].includes(probe?.status))return;
    const currentGeneration=generation.current;
    setBusy(true);setError('');
    try{
      const accepted=await api(runId?`/api/runs/${runId}/reconcile`:`/api/tasks/${taskId}/self-check`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf||''})});
      if(generation.current===currentGeneration)setProbe(accepted);
    }catch(value){if(generation.current===currentGeneration)setError(value.message||'检查未完成')}
    finally{if(generation.current===currentGeneration)setBusy(false)}
  };
  const pending=busy||['queued','running'].includes(probe?.status);
  const text=pending?'检查中…':label;
  return <>{menuItem?<RowActionItem className="row-menu-item" closeOnClick={false} onClick={start} disabled={pending||!taskId}>{text}</RowActionItem>:<button type="button" className="button quiet" onClick={start} disabled={pending||!taskId}>{text}</button>}{error&&<small className="task-probe-feedback" role="alert">{error}</small>}{probe?.result?.summary&&<small className="task-probe-feedback" role="status">{probe.result.summary}</small>}{probe&&['queued','running'].includes(probe.status)&&<small className="task-probe-feedback" role="status">等待账号空闲进行只读检查，不会发送消息</small>}</>;
}


return TaskProbeAction;
}
