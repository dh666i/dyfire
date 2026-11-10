const count=value=>['number','string'].includes(typeof value)&&String(value).trim()&&Number.isSafeInteger(Number(value))&&Number(value)>=0?Number(value):null;
const dayTime=value=>{
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return null;
  const at=Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(at)&&new Date(at).toISOString().slice(0,10)===value?at:null;
};
export const deliveryDate=date=>dayTime(date)===null?'日期未知':`${Number(date.slice(5,7))}月${Number(date.slice(8,10))}日`;

export function deliverySeries(trend) {
  const days=new Map();
  for(const item of Array.isArray(trend)?trend:[]) {
    if(!item||dayTime(item.date)===null)continue;
    const total=count(item.total),success=count(item.success),failed=count(item.failed),skipped=count(item.skipped);
    const known=[total,success,failed,skipped].every(value=>value!==null)&&success+failed+skipped<=total;
    days.set(item.date,{date:item.date,total,success,failed,skipped,other:known?total-success-failed-skipped:null,known});
  }
  const rows=[...days.values()].sort((a,b)=>a.date.localeCompare(b.date));
  if(!rows.length)return {days:[],maximum:0,total:0,knownDays:0,missingDays:0,other:0};
  const start=dayTime(rows[0].date),end=dayTime(rows.at(-1).date);
  // A missing calendar day occupies a real empty slot, never a zero-height
  // success bar. The API range is at most 30 days; cap malformed long ranges.
  const points=end-start<=365*86400000?Array.from({length:Math.round((end-start)/86400000)+1},(_,index)=>{
    const date=new Date(start+index*86400000).toISOString().slice(0,10);
    return days.get(date)||{date,total:null,success:null,failed:null,skipped:null,other:null,known:false};
  }):rows;
  const known=points.filter(point=>point.known);
  return {days:points,maximum:Math.max(0,...known.map(point=>point.total)),total:known.reduce((sum,point)=>sum+point.total,0),knownDays:known.length,missingDays:points.length-known.length,other:known.reduce((sum,point)=>sum+point.other,0)};
}

export function deliveryScale(maximum) {
  if(maximum<=2)return 2;
  const base=10**Math.floor(Math.log10(maximum));
  return [1,2,5,10].find(factor=>factor*base>=maximum)*base;
}

export function deliveryKeyIndex(length,current,key) {
  if(!length)return null;
  if(key==='Home')return 0;
  if(key==='End')return length-1;
  if(!['ArrowLeft','ArrowRight'].includes(key))return current;
  if(current===null||current<0)return length-1;
  return Math.max(0,Math.min(length-1,current+(key==='ArrowLeft'?-1:1)));
}

export function deliveryDescription(day) {
  if(!day)return '';
  return day.known?`${deliveryDate(day.date)}，共 ${day.total} 次，已发出 ${day.success} 次，失败或待核实 ${day.failed} 次，已跳过 ${day.skipped} 次${day.other?`，其他状态 ${day.other} 次`:''}`:`${deliveryDate(day.date)}，尚未收到完整统计`;
}
