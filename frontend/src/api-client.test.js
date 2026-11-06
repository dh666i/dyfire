import test from 'node:test';
import assert from 'node:assert/strict';
import {createApiClient} from './api-client.js';
const ok=value=>({ok:true,status:200,headers:new Headers({'content-type':'application/json'}),json:async()=>value});
test('simultaneous reads coalesce but later refresh reads the server again',async()=>{
  let calls=0,resolve;
  const api=createApiClient({fetcher:()=>{calls++;return new Promise(r=>resolve=r)}});
  const a=api('/bootstrap'),b=api('/bootstrap');
  assert.equal(calls,1);resolve(ok({version:1}));assert.deepEqual(await a,await b);
  const c=api('/bootstrap');assert.equal(calls,2);resolve(ok({version:2}));assert.equal((await c).version,2);
});
test('writes are never coalesced or automatically retried after lost response',async()=>{
  let calls=0;const api=createApiClient({fetcher:async()=>{calls++;throw new TypeError('network')}});
  await assert.rejects(api('/tasks',{method:'POST'}),e=>e.outcomeUnknown===true&&e.code==='NETWORK_ERROR');
  assert.equal(calls,1);
});
test('route cancellation cannot abort another reader',async()=>{
  let resolve;const api=createApiClient({fetcher:(_p,o)=>new Promise((r,reject)=>{resolve=r;o.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')))})});
  const controller=new AbortController();const abandoned=api('/page',{signal:controller.signal});controller.abort();
  await assert.rejects(abandoned,e=>e.name==='AbortError');
  const other=api('/page');resolve(ok('current'));assert.equal(await other,'current');
});
test('API errors retain status and server support reference',async()=>{
  const api=createApiClient({fetcher:async()=>({ok:false,status:409,headers:new Headers({'content-type':'application/json','x-request-id':'safe-reference'}),json:async()=>({detail:'目标已存在'})})});
  await assert.rejects(api('/tasks'),e=>e.status===409&&e.message==='目标已存在'&&e.requestId==='safe-reference');
});
