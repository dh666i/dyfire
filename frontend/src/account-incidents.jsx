import React,{useEffect,useState} from 'react';

export default function AccountIncidents({account,onClose,api,Drawer,Badge,LoadingState}) {
  const [state,setState]=useState({loading:true,items:[],error:''});
  useEffect(()=>{
    if(!account?.id)return;
    const controller=new AbortController();setState({loading:true,items:[],error:''});
    api(`/api/accounts/${encodeURIComponent(account.id)}/incidents`,{signal:controller.signal})
      .then(value=>{if(!controller.signal.aborted)setState({loading:false,items:value.items||[],error:''})})
      .catch(error=>{if(error.name!=='AbortError')setState({loading:false,items:[],error:error.message})});
    return()=>controller.abort();
  },[account?.id,api]);
  const date=value=>value?new Date(value).toLocaleString('zh-CN'):'—';
  const mail={queued:'邮件提醒已排队',sending:'正在投递提醒',accepted:'邮件服务器已接受提醒',failed:'邮件提醒投递失败',uncertain:'提醒投递结果待核对',cancelled:'邮件提醒已取消',expired_record:'邮件记录已过保留期',not_queued:'未安排邮件提醒'};
  return <Drawer open={!!account} onClose={onClose} title="账号异常记录" eyebrow="ACCOUNT HISTORY" centered>
    <div className="drawer-content form-stack">
      <p className="muted-copy">{account?.name} · 同一次持续异常合并显示。重复检查不会增加事件；任务保持原设置。</p>
      {state.loading?<LoadingState label="正在读取异常记录…"/>:state.error?<p role="alert">{state.error}</p>:state.items.length?state.items.map(item=><section className="workspace-section-card" key={item.id}>
        <div className="section-head"><b>{date(item.started_at)}</b><Badge tone={item.open?'warn':'good'}>{item.open?'待恢复':'已恢复'}</Badge></div>
        <p>{item.affected_tasks} 个受影响任务 · {item.recovery_actions} 次用户复核操作 · {Math.ceil(item.duration_seconds/60)} 分钟</p>
        <small className="muted">{mail[item.email_notice]||'提醒状态未知'}</small>
        <small className="muted">{item.resolved_at?`恢复于 ${date(item.resolved_at)}`:`最近检查 ${date(item.last_seen_at)}`}</small>
      </section>):<p className="muted-copy">尚无记录。此处从功能启用后开始记录，历史执行结果仍可在执行记录中查看。</p>}
    </div>
  </Drawer>;
}
