import React,{useId,useMemo,useState} from 'react';
import {deliverySeries,deliveryScale,deliveryDate,deliveryKeyIndex,deliveryDescription} from './delivery-trend.js';
import './delivery-trend.css';

const categories=[['success','已发出'],['failed','失败 / 待核实'],['skipped','已跳过']];

export default function DeliveryTrend({trend,loading=false,error=''}) {
  const data=useMemo(()=>deliverySeries(trend),[trend]);
  const [selection,setSelection]=useState(null);
  const hintId=useId();
  const {days,knownDays,missingDays,maximum,total,other}=data;
  const selectedIndex=selection?days.findIndex(day=>day.date===selection.date):-1;
  const active=selectedIndex<0?null:days[selectedIndex];
  const scale=deliveryScale(maximum);
  const middle=Math.floor(scale/2);
  const values=other?[...categories,['other','其他状态']]:categories;
  const selectAt=(event,pinned)=>{
    if(!days.length)return;
    const rect=event.currentTarget.getBoundingClientRect();
    const index=Math.max(0,Math.min(days.length-1,Math.floor((event.clientX-rect.left)/Math.max(1,rect.width)*days.length)));
    setSelection({date:days[index].date,source:'pointer',pinned});
  };
  const keyboard=event=>{
    if(event.key==='Escape'){event.preventDefault();setSelection(null);return}
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const index=deliveryKeyIndex(days.length,selectedIndex,event.key);
    if(index!==null)setSelection({date:days[index].date,source:'keyboard',pinned:false});
  };
  const empty=loading?['正在读取发送趋势','收到执行统计后，图表会自动显示。']:error?['发送趋势读取失败','暂时无法获得最新统计，稍后会自动重试。']:['暂无执行统计','此时段还没有返回有效统计；空白不代表执行次数为 0。'];
  return <div className="delivery-trend" aria-label="发送趋势图" aria-busy={loading}>
    {days.length>0&&<div className="delivery-chart-caption"><span>{deliveryDate(days[0].date)} — {deliveryDate(days.at(-1).date)}</span><span>单位：次 · 北京时间</span></div>}
    {knownDays>0?<>
      {(loading||error||missingDays>0)&&<p className={`delivery-read-state${error?' is-error':''}`} role="status">{error?'暂时未能更新，保留上次读取的统计。':loading?'正在更新统计…':`${missingDays} 天统计未完整返回，已保留空白。`}</p>}
      <div className="delivery-plot">
        <div className="delivery-y-axis" aria-hidden="true">{[scale,middle,0].map(value=><span key={value} style={{top:`${100-value/scale*100}%`}}>{value}</span>)}</div>
        <div className="delivery-chart-surface" role="slider" tabIndex={0} aria-label="执行统计日期" aria-describedby={hintId} aria-valuemin={0} aria-valuemax={days.length-1} aria-valuenow={Math.max(0,selectedIndex)} aria-valuetext={deliveryDescription(active||days[0])} onPointerMove={event=>{if(event.pointerType!=='touch'&&!selection?.pinned)selectAt(event,false)}} onClick={event=>selectAt(event,true)} onPointerLeave={()=>setSelection(current=>current?.source==='pointer'&&!current.pinned?null:current)} onFocus={()=>{if(!active)setSelection({date:days.at(-1).date,source:'keyboard',pinned:false})}} onBlur={()=>setSelection(current=>current?.pinned?current:null)} onKeyDown={keyboard}>
          <div className="delivery-grid" aria-hidden="true">{[scale,middle,0].map(value=><span key={value} style={{top:`${100-value/scale*100}%`}}/>)}</div>
          <div className="delivery-bars" aria-hidden="true" style={{gridTemplateColumns:`repeat(${days.length},minmax(0,1fr))`}}>{days.map((day,index)=><div key={day.date} className={`delivery-day${selectedIndex===index?' is-inspected':''}${!day.known?' is-missing':''}`}>
            {day.known?<div className="delivery-stack">{values.map(([key])=>day[key]>0&&<i key={key} className={`is-${key}`} style={{height:`${day[key]/scale*100}%`}}/>)}{day.total===0&&<span className="delivery-zero"/>}</div>:<span className="delivery-missing"/>}
          </div>)}</div>
          {active&&<div className="delivery-tooltip" aria-hidden="true" style={{'--delivery-position':`${(selectedIndex+.5)/days.length*100}%`}}><small>{deliveryDate(active.date)}</small><strong>{active.known?`共 ${active.total} 次执行`:'统计未采集'}</strong>{active.known?values.map(([key,label])=><span key={key}><i className={`is-${key}`}/>{label}<b>{active[key]}</b></span>):<p>此日没有完整数据，不按 0 次统计。</p>}</div>}
        </div>
      </div>
      <div className="delivery-x-axis" aria-hidden="true"><span>{deliveryDate(days[0].date)}</span>{days.length>2&&<span>{deliveryDate(days[Math.floor((days.length-1)/2)].date)}</span>}{days.length>1&&<span>{deliveryDate(days.at(-1).date)}</span>}</div>
      {total===0&&!loading&&!error&&<p className="delivery-zero-note">此时段暂无执行，已返回的 {knownDays} 天统计均为 0 次。</p>}
      <div className="delivery-chart-help"><span id={hintId}>悬停查看 · 点按锁定 · 方向键切换 · Esc 取消</span>{selection?.pinned&&<button type="button" onClick={()=>setSelection(null)}>取消锁定</button>}</div>
      <span className="delivery-sr-only" role="status" aria-live="polite">{selection?.source==='keyboard'||selection?.pinned?deliveryDescription(active):''}</span>
    </>:<div className={`delivery-empty${loading?' is-loading':''}${error?' is-error':''}`} role="status"><span aria-hidden="true">{loading?<i/>:<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4v16h16M9 15v-4m5 4V7m5 8v-6"/></svg>}</span><b>{empty[0]}</b><p>{empty[1]}</p></div>}
    <div className="delivery-legend" aria-label="执行状态图例">{values.map(([key,label])=><span key={key}><i className={`is-${key}`}/>{label}</span>)}</div>
  </div>;
}
