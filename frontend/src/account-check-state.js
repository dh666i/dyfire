export function accountNeedsRecovery(account) {
  const state=account?.health?.state||account?.validation_state;
  return Boolean(account?.health?.blocked)||['verification','invalid','relogin'].includes(state);
}

// A queued request is never proof of validity. Only a newer server check can
// settle it; elapsed time merely stops polling and leaves an explicit unknown.
export function accountCheckResult(account,request,now=Date.now()) {
  if(!request)return null;
  if(!['pending','waiting'].includes(request.state))return request;
  const health=account?.health||{};
  const state=health.state||account?.validation_state;
  const checked=health.checked_at;
  if(checked&&checked!==request.previousChecked&&new Date(checked).getTime()>=request.startedAt-1000){
    // Enqueuing a check itself writes temporary + blocked + checked_at. Those
    // fields only pause scheduling; they do not prove a user challenge exists.
    if(!health.checking&&!health.next_check_at){
      if(['verification','invalid'].includes(state))return {...request,state:'verification',message:'检查完成：请打开验证，按抖音页面提示操作。'};
      if(state==='valid'&&!health.blocked)return {...request,state:'valid',message:'检查完成：登录有效，后续计划按原设置执行。'};
    }
  }
  if(now-request.startedAt>=60000)return {...request,state:'waiting',message:'暂未收到明确结果，请稍后刷新查看。'};
  return request;
}
