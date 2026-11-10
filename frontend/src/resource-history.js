export const metricValue = value => {
  if (!['number','string'].includes(typeof value) || (typeof value === 'string' && !value.trim())) return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
};

const historyPoints = history => (Array.isArray(history?.points) ? history.points : [])
  .filter(point => point && metricValue(point.at) !== null)
  .map(point => ({...point,at:Number(point.at)})).sort((a,b) => a.at-b.at);

// The server supplies aggregated peaks, not raw observations. Smaller views
// retain that resolution and never interpolate or synthesize missing buckets.
export function resourceWindow(history, hours=24) {
  const points=historyPoints(history);
  const duration=[1,6,24].includes(Number(hours))?Number(hours):24;
  const end=metricValue(history?.end)??metricValue(history?.latest_at)??points.at(-1)?.at??0;
  const requestedStart=end-duration*3600;
  const declaredStart=metricValue(history?.start);
  const start=declaredStart!==null&&declaredStart<end?Math.max(declaredStart,requestedStart):requestedStart;
  const bucket=Math.max(1,metricValue(history?.bucket_seconds)||120);
  return {...history,start,end,bucket_seconds:bucket,points:points.filter(point=>point.at>=start&&point.at<=end)};
}

export function resourceSeries(history, key) {
  const points = historyPoints(history);
  const values = points.map(point => metricValue(point[key])).filter(value => value !== null);
  const latest = points.at(-1);
  return {
    points,
    // A missing latest value must remain missing, not become zero or an older reading.
    current: metricValue(latest?.[key]),
    peak: values.length ? Math.max(...values) : null,
    average: values.length ? values.reduce((sum,value)=>sum+value,0)/values.length : null,
    validCount:values.length,
  };
}

export function resourceAvailability(history,key) {
  if (!history) return {state:'loading',label:'正在读取',title:'正在读取监控数据',detail:'收到服务器采样后，走势会自动显示。'};
  if (history.state==='unavailable') return {state:'error',label:'读取失败',title:'暂时无法读取监控数据',detail:'正在自动重试；已有曲线为上次读取的记录。'};
  const summary=resourceSeries(history,key);
  if (!summary.points.length) return {state:'empty',label:'尚未采集',title:'所选时段暂无采样',detail:'采集开始后会自动显示；没有采样不代表使用率为 0%。'};
  if (history.state==='stale') return {state:'stale',label:'数据已过期',title:'采样暂未更新',detail:'当前显示历史采样，不能代表服务器现在的使用率。'};
  if (!summary.validCount) return {state:'missing',label:'指标缺测',title:'此指标暂无有效采样',detail:'其他指标可能已有数据。缺测会保留空白，不按 0% 绘制。'};
  if (history.state==='partial'||summary.current===null) return {state:'partial',label:'部分缺测',title:'部分指标尚未返回',detail:'缺测区间保留空白，已采集到的真实数值继续显示。'};
  return {state:'fresh',label:'采集中',title:'正在持续采样',detail:''};
}

// Inspect the bucket beneath the pointer. Nearest-point snapping would make
// an outage look like an old valid reading stretched across the empty period.
export function resourceSampleAt(points,target,bucketSeconds) {
  if (!points.length||!Number.isFinite(target)) return null;
  let low=0,high=points.length-1,index=-1;
  while(low<=high) {
    const middle=Math.floor((low+high)/2);
    if(points[middle].at<=target){index=middle;low=middle+1}else high=middle-1;
  }
  if(index<0||target-points[index].at>=Math.max(1,bucketSeconds))return null;
  return points[index];
}

export function resourceKeyTime(points,selectedAt,key) {
  if (!points.length) return null;
  if (key==='Home') return points[0].at;
  if (key==='End') return points.at(-1).at;
  if (!['ArrowLeft','ArrowRight'].includes(key)) return selectedAt;
  if (selectedAt===null) return points.at(-1).at;
  if (key==='ArrowLeft') return points.findLast(point=>point.at<selectedAt)?.at??points[0].at;
  return points.find(point=>point.at>selectedAt)?.at??points.at(-1).at;
}

export function formatResourcePercent(value) {
  const number=metricValue(value);
  return number===null?'未采集':`${Number(number.toFixed(1))}%`;
}

export function formatResourceTime(value,{date=true}={}) {
  const number=metricValue(value);
  if(number===null||!Number.isFinite(new Date(number*1000).getTime()))return '—';
  const moment=new Date(number*1000);
  const clock=moment.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  if(!date)return clock;
  const parts=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric'}).formatToParts(moment);
  return `${parts.find(part=>part.type==='month').value}月${parts.find(part=>part.type==='day').value}日 ${clock}`;
}

export function resourcePath(points, key, start, end, gapSeconds) {
  let previous = null;
  return points.map(point => {
    const value = metricValue(point[key]);
    if (value === null || metricValue(point.at)===null || point.at<start || point.at>end) { previous = null; return ''; }
    const x = (point.at-start)/Math.max(1,end-start)*600;
    const y = 100-Math.min(100,Math.max(0,value));
    const command = previous === null || point.at-previous > gapSeconds ? 'M' : 'L';
    previous = point.at;
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}
