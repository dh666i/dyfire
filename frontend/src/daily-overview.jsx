import React from 'react';
import {Link} from 'react-router-dom';
import {Button, LiveNumber, StatusIcon} from './ui-controls.jsx';

const displayName=account=>account.name||account.profile?.nickname||'抖音账号';
const healthy=account=>!account.health?.blocked&&(account.health?.state==='valid'||account.validation_state==='valid');
const needsVerification=account=>['verification','invalid','relogin'].includes(account.health?.state)||['verification','invalid'].includes(account.validation_state);

// All figures describe the supplied execution records, never an estimated rate.
export default function DailyOverview({accounts=[],runs=[],limits={},onAccountTasks,onCreate}) {
  const counts={success:0,running:0,unconfirmed:0,failed:0,other:0};
  for(const run of runs) {
    const key=run.status==='success'?'success':run.status==='running'?'running':run.status==='submitted_unconfirmed'?'unconfirmed':run.status==='failed'?'failed':'other';
    counts[key]++;
  }
  const valid=accounts.filter(healthy).length;
  const attention=accounts.filter(account=>!healthy(account));
  const verification=attention.filter(needsVerification);
  const statuses=[['success','已发出'],['running','执行中'],['unconfirmed','待核实'],['failed','未发出']];
  const accountTotal=limits.accounts_total??accounts.length;
  return <section className="daily-overview" aria-label="今日续火概览" data-reveal="section">
    <div className="daily-execution">
      <div className="daily-section-head"><span className="daily-label">{runs.length>=80?'今日最近 80 次执行':'今日执行'}</span><Link className="text-action" to="/runs">查看记录<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div>
      <div className="daily-main-number"><strong><LiveNumber value={counts.success}/></strong><div><span>次已确认发送</span><small>{runs.length?`共 ${runs.length} 次执行记录`:'任务执行后，结果会显示在这里'}</small></div></div>
      <div className="daily-progress" role="img" aria-label={`今日记录：${statuses.map(([key,label])=>`${counts[key]} 次${label}`).join('，')}${counts.other?`，${counts.other} 次其他状态`:''}`}>
        {Object.entries(counts).map(([key,count])=>count>0&&<span key={key} className={`is-${key}`} style={{flex:count}}/>)}
      </div>
      <div className="daily-statuses">{statuses.map(([key,label])=><Link key={key} className={`daily-stat is-${key}`} aria-label={`${label} ${counts[key]} 次`} to={`/runs?status=${key==='unconfirmed'?'submitted_unconfirmed':key}`}><span><i/>{label}</span><b><LiveNumber value={counts[key]}/></b></Link>)}</div>
      {counts.other>0&&<Link className="daily-other text-action" to="/runs">另有 {counts.other} 次其他状态，查看记录<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link>}
    </div>
    <div className="daily-accounts">
      <div className="daily-section-head"><h2>抖音账号</h2><Link className="text-action" to="/accounts">管理账号<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div>
      <div className="daily-account-total"><strong>{valid}<span> / {accounts.length}</span></strong><span>{limits.accounts_truncated?'当前列表登录有效':'登录有效'}</span></div>
      {accounts.length?<div className="daily-account-list">{[...accounts].sort((a,b)=>Number(healthy(a))-Number(healthy(b))).slice(0,3).map(account=><div className="daily-account-row" key={account.id}>
        <span className="avatar">{account.profile?.avatar_url?<img src={account.profile.avatar_url} alt=""/>:displayName(account).slice(0,1)}</span>
        <div><b>{displayName(account)}</b><small className={healthy(account)?'good-text':'warning-text'}><i/>{account.health?.label||(healthy(account)?'登录有效':'需检查登录')}</small></div>
        {healthy(account)?<button type="button" className="daily-account-action" onClick={event=>onAccountTasks(account,event)} aria-label={`查看 ${displayName(account)} 的任务`}>查看任务</button>:<Link className="daily-account-action" to={`/accounts#account-${account.id}`} aria-label={`处理 ${displayName(account)} 的账号状态`}>处理</Link>}
      </div>)}</div>:<div className="daily-no-account"><p>绑定后即可开始每日续火</p><Button onClick={onCreate}>绑定抖音账号</Button></div>}
      {accountTotal>3&&<Link className="daily-all-accounts text-action" to="/accounts">查看全部 {accountTotal} 个账号<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link>}
    </div>
    <div className={`daily-attention${attention.length||counts.failed||counts.unconfirmed?' has-attention':''}`}>
      <StatusIcon kind={attention.length||counts.failed||counts.unconfirmed?'warning':'success'}/>
      <div><b>{attention.length?`${attention.length} 个账号需关注`:counts.unconfirmed?'有发送结果等待核实':counts.failed?'有任务未发出':accounts.length?'当前没有待处理的异常':'先绑定账号，再创建任务'}</b><span>{attention.length?(verification.length===attention.length?'完成账号验证后，后续计划会继续执行。':'查看账号的最新检查结果和处理方式。'):counts.unconfirmed?'请先确认聊天记录，避免重复发送。':counts.failed?'打开执行记录查看原因和处理方式。':accounts.length?'执行状态和需要处理的事项会集中显示在这里。':'支持从已同步会话选择好友。'}</span></div>
      {(attention.length>0||counts.failed>0||counts.unconfirmed>0)&&<Link className="daily-attention-action" to={attention.length?`/accounts#account-${attention[0].id}`:counts.unconfirmed?'/runs?status=submitted_unconfirmed':'/runs?status=failed'}>去处理</Link>}
    </div>
  </section>;
}
