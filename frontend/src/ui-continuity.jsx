import React, {useEffect, useRef, useState} from 'react';
import {StatusIcon} from './ui-icons.jsx';

export function FilterSummary({items=[],onClear,label='当前筛选'}) {
  const active=items.filter(item=>item.value);
  if(!active.length)return null;
  return <div className="filter-summary" role="group" aria-label={label}>
    {active.map(item=><button type="button" key={item.key} className="filter-chip" onClick={item.onRemove} aria-label={`移除${item.label}筛选：${item.value}`}><span>{item.label}：{item.value}</span><StatusIcon kind="close"/></button>)}
    <button className="filter-clear" type="button" onClick={onClear}>清除筛选</button>
  </div>;
}

export function ListSkeleton({label='正在读取列表…',rows=3}) {
  return <div className="list-skeleton" role="status" aria-label={label} aria-busy="true"><span className="sr-only">{label}</span>{Array.from({length:rows},(_,i)=><div key={i} aria-hidden="true"><i/><span><b/><small/></span><em/></div>)}</div>;
}

export function ReadStatus({busy,error,lastUpdated,hasData=true}) {
  return <div className={`read-status${error?' is-error':''}`} role={error?'alert':'status'} aria-live="polite">
    {busy?<><StatusIcon kind="loading" className="read-spinner"/><span>{hasData?'正在更新，保留当前内容':'正在读取…'}</span></>:error?<><StatusIcon kind="warning"/><span>更新未完成，保留上次结果</span></>:lastUpdated?<span>更新于 {new Date(lastUpdated).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}</span>:null}
  </div>;
}

// Persistent errors and short confirmed-success feedback live beside the row.
// The ref lock closes the interval before React commits a disabled button.
export function useRowFeedback() {
  const [states,setStates]=useState({});
  const locks=useRef(new Set()), timers=useRef(new Map()), mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;timers.current.forEach(clearTimeout)}},[]);
  const set=(id,value)=>{if(mounted.current)setStates(current=>({...current,[id]:value}))};
  const mark=(id,message)=>{
    clearTimeout(timers.current.get(id));set(id,{kind:'success',message});
    timers.current.set(id,setTimeout(()=>{if(mounted.current)setStates(current=>{const next={...current};if(next[id]?.kind==='success')delete next[id];return next})},4200));
  };
  const run=async(id,request,onSuccess,label)=>{
    if(locks.current.has(id))return;
    locks.current.add(id);clearTimeout(timers.current.get(id));set(id,{kind:'pending',message:'正在保存…'});
    try{const result=await request();await onSuccess?.(result);mark(id,label);return result}
    catch(error){set(id,{kind:'error',message:error.message||'未能保存，请重试'});return null}
    finally{locks.current.delete(id)}
  };
  return {states,run,mark};
}

export function RowFeedback({value}) {
  if(!value)return null;
  return <span className={`row-feedback is-${value.kind}`} role={value.kind==='error'?'alert':'status'}><StatusIcon kind={value.kind==='pending'?'loading':value.kind==='error'?'warning':'check'}/>{value.message}</span>;
}
