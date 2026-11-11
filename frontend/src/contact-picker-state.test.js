import test from 'node:test';
import assert from 'node:assert/strict';
import {contactPageMetadata, createContactPickerController} from './contact-picker-state.js';

const contacts = count => Array.from({length:count}, (_, i) => ({sec_uid:`friend-${i}`, name:`好友 ${i}`}));
const snapshot = (count, extra={}) => ({items:contacts(count), total:count, has_more:false, next_offset:count, sync_has_more:null, can_sync_more:true, sync_next_limit:Math.min(100, count+20), ...extra});
const deferred = () => { let resolve; const promise=new Promise(done=>{resolve=done});return {promise,resolve}; };

test('unknown cached contacts retain a sync action and accept only supported limits', () => {
  for(const [count, expected] of [[0,20],[18,20],[20,40],[40,60],[41,60],[100,100],[125,100]]) {
    const page=contactPageMetadata({items:contacts(count), total:count, sync_has_more:null, sync_limit:0},count);
    assert.equal(page.canSyncMore,true);
    assert.equal(page.nextLimit,expected);
  }
  const exhausted=contactPageMetadata(snapshot(18,{sync_has_more:false,can_sync_more:false,sync_next_limit:null,sync_limit:20}));
  assert.equal(exhausted.canSyncMore,false);
  assert.equal(exhausted.refreshLimit,20);
});

test('latest sync exposes bounded streak read counts for clear empty feedback', () => {
  const page=contactPageMetadata({
    ...snapshot(100),
    sync:{status:'succeeded',limit:100,has_more:false,streak_diagnostics:{matched_observations:0,init_responses:1}},
  });
  assert.equal(page.syncStatus,'succeeded');
  assert.deepEqual(page.streakDiagnostics,{matched_observations:0,init_responses:1});
});

test('selecting from 20 cached rows, clicking sync and completing the job yields 40 without losing selection', async () => {
  const calls=[];
  let clock=0, reads=0;
  const controller=createContactPickerController({
    request:async(url,options)=>{
      calls.push([url,options]);
      if(url.includes('/conversations?'))return reads++?snapshot(40):snapshot(20);
      if(options.method==='POST')return {id:'new-job',status:'queued',limit:40};
      return {id:'new-job',status:'succeeded',limit:40};
    },
    now:()=>clock, wait:async ms=>{clock+=ms},
  });
  await controller.open('account','csrf');
  controller.select('friend-3',true);
  assert.equal(controller.getState().page.canSyncMore,true);
  await controller.next();
  const post=calls.find(([,options])=>options.method==='POST');
  assert.equal(post[1].body.get('limit'),'40');
  assert.equal(post[1].body.get('csrf_token'),'csrf');
  assert.equal(controller.getState().items.length,40);
  assert.deepEqual([...controller.getState().selected],['friend-3']);
});

test('reopening resumes an active job beyond 40 seconds without enqueuing another', async () => {
  let clock=0, polls=0, reads=0, posts=0;
  const controller=createContactPickerController({
    now:()=>clock, pollMs:5000, wait:async ms=>{clock+=ms},
    request:async(url,options)=>{
      if(options.method==='POST'){posts++;throw new Error('unexpected enqueue')}
      if(url.includes('/conversations?'))return reads++?snapshot(40):snapshot(20,{active_sync:{id:'existing',status:'running',limit:40},can_sync_more:false,sync_next_limit:null});
      return {id:'existing',status:++polls<15?'running':'succeeded'};
    },
  });
  await controller.open('account');
  assert.equal(clock,75000);
  assert.equal(posts,0);
  assert.equal(controller.getState().items.length,40);
  assert.equal(controller.getState().busy,'');
});

test('cached pagination is consumed before requesting a directory sync', async () => {
  let reads=0;
  const calls=[];
  const controller=createContactPickerController({request:async(url,options)=>{
    calls.push([url,options]);
    return reads++?{...snapshot(120),items:contacts(120).slice(100),next_offset:120}:snapshot(100,{total:120,has_more:true,next_offset:100});
  }});
  await controller.open('account');
  controller.select('friend-1',true);
  await controller.next();
  assert.match(calls[1][0],/offset=100&limit=100$/);
  assert.equal(calls.filter(([,options])=>options.method==='POST').length,0);
  assert.equal(controller.getState().items.length,120);
  assert.equal(controller.getState().selected.has('friend-1'),true);
});

test('a failed sync preserves selectable cached rows and selected contacts', async () => {
  const controller=createContactPickerController({request:async(url,options)=>{
    if(url.includes('/conversations?'))return snapshot(20);
    if(options.method==='POST')return {id:'failed-job',status:'failed',error:'请先完成抖音验证'};
    throw new Error('unexpected poll');
  }});
  await controller.open('account');
  controller.select('friend-4',true);
  await controller.next();
  assert.equal(controller.getState().items.length,20);
  assert.equal(controller.getState().selected.has('friend-4'),true);
  assert.equal(controller.getState().error,'请先完成抖音验证');
  assert.equal(controller.getState().busy,'');
  controller.select('friend-5',true);
  assert.equal(controller.getState().selected.size,2);
});

test('a transient poll failure resumes the accepted job on retry instead of repeating the enqueue', async () => {
  let posts=0, polls=0, reads=0;
  const controller=createContactPickerController({wait:async()=>{},request:async(url,options)=>{
    if(url.includes('/conversations?'))return reads++?snapshot(40):snapshot(20);
    if(options.method==='POST'){posts++;return {id:'accepted-job',status:'queued'}}
    if(polls++===0)throw new Error('网络暂时中断');
    return {id:'accepted-job',status:'succeeded'};
  }});
  await controller.open('account');
  controller.select('friend-2',true);
  await controller.next();
  assert.equal(controller.getState().page.activeSync.id,'accepted-job');
  assert.match(controller.getState().error,/网络/);
  await controller.next();
  assert.equal(posts,1);
  assert.equal(controller.getState().items.length,40);
  assert.equal(controller.getState().selected.has('friend-2'),true);
});

test('double clicks enqueue once, and closing ignores a late successful response', async () => {
  const pending=deferred();
  let posts=0, reads=0;
  const controller=createContactPickerController({request:async(url,options)=>{
    if(url.includes('/conversations?')){reads++;return snapshot(20)}
    if(options.method==='POST'){posts++;return pending.promise}
    throw new Error('should not follow a closed session');
  }});
  await controller.open('old-account');
  controller.select('friend-2',true);
  const first=controller.next();
  await controller.next();
  assert.equal(posts,1);
  controller.close();
  pending.resolve({id:'late-job',status:'succeeded'});
  await first;
  assert.equal(reads,1);
  assert.equal(controller.getState().items.length,20);
  assert.equal(controller.getState().selected.has('friend-2'),true);
});

test('switching accounts cannot be overwritten by a slow response from the previous account', async () => {
  const pending=deferred();
  const controller=createContactPickerController({request:async url=>url.includes('/old/')?pending.promise:snapshot(40)});
  const old=controller.open('old');
  await controller.open('new');
  controller.select('friend-9',true);
  pending.resolve(snapshot(20));
  await old;
  assert.equal(controller.getState().items.length,40);
  assert.equal(controller.getState().selected.has('friend-9'),true);
});

test('explicit exhaustion retains refresh, and polling stops at the five-minute job deadline', async () => {
  let clock=0, postLimit;
  const controller=createContactPickerController({
    now:()=>clock, pollMs:30000, wait:async ms=>{clock+=ms},
    request:async(url,options)=>{
      if(url.includes('/conversations?'))return snapshot(18,{sync_has_more:false,can_sync_more:false,sync_next_limit:null,sync_limit:20});
      if(options.method==='POST'){postLimit=options.body.get('limit');return {id:'job',status:'queued'}}
      return {id:'job',status:'running'};
    },
  });
  await controller.open('account');
  controller.select('friend-7',true);
  await controller.next();
  assert.equal(postLimit,'20');
  assert.equal(clock,300000);
  assert.match(controller.getState().error,/未完成/);
  assert.equal(controller.getState().busy,'');
  assert.equal(controller.getState().selected.has('friend-7'),true);
});
