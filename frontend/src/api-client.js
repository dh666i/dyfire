// One transport implementation for pages and hooks. Never retry a write whose
// response was lost: the server may already have committed the operation.
export function createApiClient({fetcher=globalThis.fetch, preview=false, mock, dev=false, onUnauthorized=()=>globalThis.location?.assign('/login')}={}) {
  const pending=new Map();
  async function request(path,options={}) {
    if(preview)return mock(path,options);
    const {timeoutMs:requested,signal,...rest}=options;
    const method=String(rest.method||'GET').toUpperCase();
    const write=!['GET','HEAD'].includes(method);
    const timeoutMs=Number.isFinite(Number(requested))?Math.max(1000,Number(requested)):(write?25000:15000);
    const controller=new AbortController();let timedOut=false;
    const abort=()=>controller.abort();
    if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(()=>{timedOut=true;controller.abort()},timeoutMs);
    let response,value;
    try {
      response=await fetcher(path,{credentials:'same-origin',cache:'no-store',...rest,signal:controller.signal});
      if(response.status!==401){
        const type=response.headers?.get?.('content-type')||'';
        value=type.includes('application/json')?await response.json():await response.text();
      }
    }catch(cause){
      if(!timedOut&&cause?.name==='AbortError')throw cause;
      if(dev&&!timedOut)return mock(path,options);
      const error=new Error(write?'请求未返回确认，结果待核对。请刷新记录后再操作。':timedOut?'读取超时，请稍后重试':'网络连接失败，请检查网络后重试');
      error.code=timedOut?'TIMEOUT':'NETWORK_ERROR';error.outcomeUnknown=write;error.cause=cause;
      throw error;
    }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
    if(response.status===401){onUnauthorized();const error=new Error('登录状态已过期');error.status=401;throw error}
    if(!response.ok){
      if(dev&&response.status>=500)return mock(path,options);
      const error=new Error(typeof value?.detail==='string'?value.detail:value?.detail?.message||value?.message||'请求未完成');
      error.status=response.status;error.payload=value;error.requestId=response.headers?.get?.('x-request-id');throw error;
    }
    return value;
  }
  return function api(path,options={}) {
    const method=String(options.method||'GET').toUpperCase();
    // A cancellable request has its own lifecycle and cannot cancel another
    // reader. Non-cancellable identical reads share only an in-flight promise.
    const share=method==='GET'&&!options.signal&&!options.headers&&!options.body;
    if(!share)return request(path,options);
    const key=path+'|'+(options.timeoutMs||'default');
    if(!pending.has(key)){
      const promise=request(path,options).finally(()=>{if(pending.get(key)===promise)pending.delete(key)});
      pending.set(key,promise);
    }
    return pending.get(key);
  };
}
