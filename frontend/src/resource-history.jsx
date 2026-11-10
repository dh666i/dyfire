import React,{useId,useMemo,useState} from 'react';
import {metricValue,resourceSeries,resourcePath,resourceWindow,resourceAvailability,resourceSampleAt,resourceKeyTime,formatResourcePercent,formatResourceTime} from './resource-history.js';
import './resource-history.css';

const series=[{key:'cpu',label:'CPU',color:'var(--orange)'},{key:'memory',label:'内存',color:'var(--green-bright)'},{key:'disk',label:'磁盘',color:'var(--state-warning)'}];
const clamp=value=>Math.max(0,Math.min(100,value));
const intervalLabel=seconds=>seconds>=60&&seconds%60===0?`${seconds/60} 分钟`:`${seconds} 秒`;

export default function ResourceHistory({history,snapshot}) {
  const [key,setKey]=useState('cpu');
  const [hours,setHours]=useState(24);
  const [selection,setSelection]=useState(null);
  const hintId=useId();
  const metric=series.find(item=>item.key===key);
  const windowed=useMemo(()=>resourceWindow(history,hours),[history,hours]);
  const summaries=useMemo(()=>Object.fromEntries(series.map(item=>[item.key,resourceSeries(windowed,item.key)])),[windowed]);
  const {points,validCount}=summaries[key];
  const {start,end,bucket_seconds:bucket}=windowed;
  const state=resourceAvailability(history?windowed:null,key);
  const selectedAt=selection&&selection.at>=start&&selection.at<=end?selection.at:null;
  const active=selectedAt===null?null:resourceSampleAt(points,selectedAt,bucket);
  const activeValue=metricValue(active?.[key]);
  const inspectedAt=active?.at??selectedAt;
  const cursorAt=selectedAt===null?null:(selectedAt-start)/Math.max(1,end-start)*100;
  const inspectionText=selectedAt===null?'':`${formatResourceTime(inspectedAt)}，${metric.label}${activeValue===null?'未采集':`区间峰值 ${formatResourcePercent(activeValue)}`}`;
  const gap=bucket*1.5;
  const isolated=points.filter((point,index)=>metricValue(point[key])!==null&&
    (!points[index-1]||metricValue(points[index-1][key])===null||point.at-points[index-1].at>gap)&&
    (!points[index+1]||metricValue(points[index+1][key])===null||points[index+1].at-point.at>gap));
  const timeAt=event=>{
    const rect=event.currentTarget.getBoundingClientRect();
    return start+Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width)))*(end-start);
  };
  const hover=event=>{
    if(event.pointerType==='touch'||selection?.pinned)return;
    setSelection({at:timeAt(event),source:'pointer',pinned:false});
  };
  const pin=event=>setSelection({at:timeAt(event),source:'pointer',pinned:true});
  const chooseMetric=value=>{setKey(value);setSelection(null)};
  const chooseRange=value=>{setHours(value);setSelection(null)};
  const keyboard=event=>{
    if(event.key==='Escape'){event.preventDefault();setSelection(null);return}
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const at=resourceKeyTime(points,active?.at??selectedAt,event.key);
    if(at!==null)setSelection({at,source:'keyboard',pinned:false});
  };
  const currentInterval=points.at(-1)?.at;
  return <section data-reveal="section" className="card resource-history resource-history-chart" aria-label="服务器资源走势">
    <div className="section-head resource-history-heading">
      <div><h2>服务器资源走势</h2><small className="muted">使用率（%） · 北京时间</small></div>
      <div className="resource-header-controls">
        <div className="resource-range" role="group" aria-label="资源走势时间范围">{[1,6,24].map(value=><button key={value} type="button" aria-pressed={hours===value} onClick={()=>chooseRange(value)}>近 {value} 小时</button>)}</div>
        <span className={`resource-state is-${state.state}`} role="status"><i aria-hidden="true"/>{state.label}</span>
      </div>
    </div>
    <div className="resource-series" role="group" aria-label="选择监控指标">{series.map(item=>{
      const summary=summaries[item.key];
      return <button key={item.key} type="button" aria-pressed={key===item.key} onClick={()=>chooseMetric(item.key)}>
        <span><i style={{background:item.color}} aria-hidden="true"/>{item.label}</span>
        <b>{summary.current===null?'—':formatResourcePercent(summary.current)}</b>
        <small>{state.state==='loading'?'正在读取采样':summary.current===null?'最近区间未采集':'最近区间峰值'}<br/>时段峰值 {state.state==='loading'?'—':formatResourcePercent(summary.peak)}</small>
      </button>;
    })}</div>
    {['error','stale','partial'].includes(state.state)&&points.length>0&&<p className={`resource-state-note is-${state.state}`} role="status">{state.detail}</p>}
    {points.length>0&&validCount>0?<>
      <div className="resource-chart-description"><span>近 {hours} 小时 · 每 {intervalLabel(bucket)} 一个区间</span><span>区间内最高使用率</span></div>
      <div className="resource-plot">
        <div className="resource-y-axis" aria-hidden="true"><span>100%</span><span>50%</span><span>0%</span></div>
        <div className="resource-chart-surface" role="slider" tabIndex={0} aria-label={`${metric.label}采样时间`} aria-describedby={hintId} aria-valuemin={start} aria-valuemax={end} aria-valuenow={selectedAt??currentInterval??end} aria-valuetext={inspectionText||`${formatResourceTime(currentInterval)}，${formatResourcePercent(summaries[key].current)}`} onPointerMove={hover} onClick={pin} onPointerLeave={()=>setSelection(current=>current?.source==='pointer'&&!current.pinned?null:current)} onFocus={()=>{if(!selection)setSelection({at:currentInterval,source:'keyboard',pinned:false})}} onBlur={()=>setSelection(current=>current?.pinned?current:null)} onKeyDown={keyboard} style={{'--resource-color':metric.color}}>
          <svg viewBox="0 0 600 100" preserveAspectRatio="none" aria-hidden="true">
            {[0,50,100].map(y=><line key={y} className="resource-grid-line" x1="0" x2="600" y1={y} y2={y}/>)}
            <path d={resourcePath(points,key,start,end,gap)} fill="none" stroke={metric.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>
            {selectedAt!==null&&<line className="resource-cursor" x1={cursorAt*6} x2={cursorAt*6} y1="0" y2="100"/>}
          </svg>
          {isolated.map(point=><span className="resource-point" key={point.at} aria-hidden="true" style={{left:`${(point.at-start)/Math.max(1,end-start)*100}%`,top:`${100-clamp(Number(point[key]))}%`}}/>)}
          {activeValue!==null&&<span className="resource-point is-active" aria-hidden="true" style={{left:`${(active.at-start)/Math.max(1,end-start)*100}%`,top:`${100-clamp(activeValue)}%`}}/>}
          {selectedAt!==null&&<div className="resource-tooltip" aria-hidden="true" style={{'--inspection-position':`${cursorAt}%`}}><small>{formatResourceTime(inspectedAt)}</small><strong>{metric.label} <b>{formatResourcePercent(activeValue)}</b></strong><span>{activeValue===null?'此区间没有有效采样':'此采样区间内的峰值'}</span></div>}
        </div>
      </div>
      <div className="resource-x-axis" aria-hidden="true"><span>{formatResourceTime(start)}</span><span>{formatResourceTime((start+end)/2,{date:false})}</span><span>{formatResourceTime(end)}</span></div>
      <div className="resource-chart-help"><span id={hintId}>悬停查看 · 点按锁定 · 方向键切换 · Esc 取消</span>{selection?.pinned&&<button type="button" onClick={()=>setSelection(null)}>取消锁定</button>}</div>
      <span className="resource-sr-only" role="status" aria-live="polite">{selection?.source==='keyboard'||selection?.pinned?inspectionText:''}</span>
    </>:<div className={`resource-empty is-${state.state}`} role="status"><span className="resource-empty-mark" aria-hidden="true">{state.state==='loading'?<i/>:<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4v16h16M8 14l3-4 4 3 5-7"/></svg>}</span><b>{state.title}</b><p>{state.detail}</p></div>}
    <p className="resource-footnote">{metricValue(history?.latest_at)!==null?`最近采样 ${formatResourceTime(history.latest_at)}。`:''}空缺不补零；真实零值显示为 0%。</p>
    {snapshot&&<p className="resource-footnote resource-snapshot-note">{snapshot.system?.scope_label||'服务器主机指标'} · 数据库 {metricValue(snapshot.database?.size_bytes)!==null?`${Math.round(Number(snapshot.database.size_bytes)/1024/1024)} MiB`:'未读取'} · 活跃登录 {snapshot.queue?.active_logins??'未读取'}</p>}
  </section>;
}
