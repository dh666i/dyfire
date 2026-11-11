import React, {useEffect, useRef, useState} from 'react';
import {Drawer} from './ui-dialog.jsx';
import {Button, StatusIcon} from './ui-controls.jsx';
import {streakHistoryPath, streakMilestones, streakSnapshot, streakStateLabel, streakSummary, streakTime} from './spark-streak-state.js';
import './spark-streak.css';

function FlameIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M13 3c1 5-3 6-3 9-1-1-2-2-2-4-3 3-4 6-3 9a7.5 7.5 0 0 0 14-1c1-5-3-9-6-13Z"/><path d="M12 14c-1 2-3 3-2 5a2.5 2.5 0 0 0 4-2"/></svg>;
}

export function SparkSummary({spark,name,onClick,compact=false}) {
  const snapshot = streakSnapshot(spark);
  const content = <><span className="streak-summary-main"><FlameIcon/><span>{snapshot.days === null ? '天数待同步' : <><b>{snapshot.days}</b> 天</>}</span><span className="streak-summary-state">{streakStateLabel(snapshot)}</span></span><span className="streak-summary-time">{snapshot.observed_at ? `同步于 ${streakTime(snapshot.observed_at)}${snapshot.stale ? ' · 待更新' : ''}` : '尚未读取平台火花'}</span></>;
  const className = `streak-summary${compact ? ' streak-summary-compact' : ''}`;
  return onClick ? <button type="button" className={className} data-streak-state={snapshot.stale?'stale':snapshot.state} aria-label={`查看 ${name || '好友'} 的火花详情`} onClick={onClick}>{content}<StatusIcon kind="chevronRight" className="streak-summary-chevron"/></button> : <span className={className} data-streak-state={snapshot.stale?'stale':snapshot.state}>{content}</span>;
}

export function SparkDetail({target,onClose,request}) {
  const [value,setValue] = useState(null);
  const [error,setError] = useState('');
  const [loading,setLoading] = useState(false);
  const [attempt,setAttempt] = useState(0);
  const [moreBusy,setMoreBusy] = useState(false);
  const sessionRef = useRef(null);
  const path = streakHistoryPath(target);
  useEffect(() => {
    const controller = new AbortController();
    sessionRef.current = controller;
    setValue(null); setError(''); setMoreBusy(false);
    if (!target || !path) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    request(path,{signal:controller.signal}).then(next => {
      if (!controller.signal.aborted) setValue({...next,requestPath:path});
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause?.message || '火花记录暂未读取，请重试');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  },[path,Boolean(target),request,attempt]);
  const visibleValue = value?.requestPath === path ? value : null;
  const snapshot = streakSnapshot(visibleValue?.current ?? target?.spark);
  const milestones = streakMilestones(visibleValue?.milestones ?? snapshot.milestones);
  const items = Array.isArray(visibleValue?.items) ? visibleValue.items.filter(Boolean) : [];
  const more = async () => {
    const controller = sessionRef.current;
    if (!controller || controller.signal.aborted || moreBusy || !visibleValue?.has_more) return;
    setMoreBusy(true);setError('');
    try {
      const next = await request(streakHistoryPath(target,visibleValue.next_offset ?? items.length),{signal:controller.signal});
      if (!controller.signal.aborted) setValue(old=>({...next,requestPath:path,current:old.current,milestones:old.milestones,items:[...(Array.isArray(old.items)?old.items:[]),...(Array.isArray(next.items)?next.items:[])]}));
    } catch(cause) { if (!controller.signal.aborted) setError(cause?.message || '记录暂未读取，请重试'); }
    finally { if (!controller.signal.aborted) setMoreBusy(false); }
  };
  const recoverCurrent=Number.isSafeInteger(snapshot.recover_current)&&snapshot.recover_current>=0?snapshot.recover_current:null;
  const recoverTotal=Number.isSafeInteger(snapshot.recover_total)&&snapshot.recover_total>0?snapshot.recover_total:null;
  return <Drawer open={Boolean(target)} title={`${target?.name || '好友'} · 火花`} onClose={onClose} centered size="streak">
    <div className="drawer-content streak-detail">
      <div className="streak-detail-hero" data-streak-state={snapshot.stale?'stale':snapshot.state}><span className="streak-hero-icon"><FlameIcon/></span><div><span className="streak-detail-kicker">抖音火花</span><strong>{snapshot.days === null ? '暂未获取天数' : <>{snapshot.days}<small> 天</small></>}</strong><span className="streak-state-label">{streakStateLabel(snapshot)}</span></div></div>
      <p className="streak-observed">{snapshot.observed_at ? `最近同步 ${streakTime(snapshot.observed_at,true)}${snapshot.stale?' · 等待更新':''}` : '同步好友会话后，显示平台实际读取到的火花。'}</p>
      <p className="streak-detail-note">任务发送结果与火花状态分别记录，已发出消息不代表火花已续上。</p>
      {!path&&<p className="streak-detail-note">此好友尚未匹配到完整会话，请先在选择好友中重新同步。</p>}
      {loading&&<div className="streak-read-status" role="status"><StatusIcon kind="loading"/>正在读取火花记录…</div>}
      {error&&<div className="streak-read-error" role="alert"><span>{error}</span><Button variant="quiet" onClick={()=>items.length?more():setAttempt(count=>count+1)}>重试</Button></div>}
      {(snapshot.state==='recovering'||snapshot.state==='broken'||snapshot.platform_text)&&<section className="streak-recovery"><h3>平台状态</h3>{snapshot.platform_text&&<p>{snapshot.platform_text}</p>}{snapshot.state==='recovering'&&recoverCurrent!==null&&recoverTotal!==null&&recoverCurrent<=recoverTotal&&<p>平台重燃进度 <b>{recoverCurrent} / {recoverTotal}</b></p>}{['recovering','broken'].includes(snapshot.state)&&<p className="streak-muted">请在抖音聊天页查看是否可重燃及当前要求，以平台提示为准。</p>}</section>}
      <section className="streak-milestones"><h3>火花里程碑</h3>{milestones.length?<ul>{milestones.map((item,index)=><li key={`${item.days}-${item.observed_at}-${index}`}><span className="streak-milestone-number">{item.days}<small> 天</small></span><div><b>{item.kind==='first_observed'?'首次读取已达到':'观测到跨越里程碑'}</b><time>{streakTime(item.observed_at,true)}</time></div></li>)}</ul>:<p className="streak-muted">{loading?'读取中…':'尚无已观测到的里程碑'}</p>}</section>
      <section className="streak-history"><h3>真实变化记录</h3>{items.length?<ol>{items.map((item,index)=><li key={item.id||`${item.observed_at}-${index}`}><span className="streak-history-dot" aria-hidden="true"/><div><b>{streakSummary(item)}</b><time>{streakTime(item.observed_at,true)}</time>{item.platform_text&&<p>{item.platform_text}</p>}</div></li>)}</ol>:<p className="streak-muted">{loading?'读取中…':error?'记录暂未加载':snapshot.observed_at?'暂无更多变化记录':'尚未读取到平台火花，不会用发送次数推算天数。'}</p>}{visibleValue?.has_more&&<Button variant="quiet" busy={moreBusy} onClick={more}>加载更多记录</Button>}</section>
    </div>
  </Drawer>;
}
