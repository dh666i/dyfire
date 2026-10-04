import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaskComposerController, isReadySchedule, mergeTaskRows} from './task-composer-state.js';

const plan = (times=['10:00']) => ({status:'ready',times,entries:times.map((time,index)=>({index,time,next_run_at:`2026-10-05T${time}:00Z`}))});

test('schedule plans must carry one matching first-run entry per friend', () => {
  assert.equal(isReadySchedule(plan(['10:00','10:07']),2),true);
  assert.equal(isReadySchedule({...plan(['10:00']),entries:[]},1),false);
  assert.equal(isReadySchedule({...plan(['10:00']),blocked:true},1),false);
});

test('a stale preview cannot replace the current selection', async () => {
  const requests=[];
  const controller=createTaskComposerController({debounceMs:0,request:(url,{signal})=>new Promise(resolve=>requests.push({url,signal,resolve}))});
  controller.configure({accountId:'a',count:1,targets:[{name:'甲',sec_uid:'u1'}],csrf:'c',mode:'manual',preferred:'10:00'});
  await new Promise(resolve=>setTimeout(resolve,0));
  controller.configure({accountId:'a',count:1,targets:[{name:'乙',sec_uid:'u2'}],csrf:'c',mode:'manual',preferred:'11:00'});
  await new Promise(resolve=>setTimeout(resolve,0));
  requests[0].resolve({...plan(['10:00']),targets:[{name:'甲',sec_uid:'u1'}]});
  requests[1].resolve({...plan(['11:00']),targets:[{name:'乙',sec_uid:'u2'}]});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(controller.getState().plan.targets,[{name:'乙',sec_uid:'u2'}]);
  controller.close();
});

test('creation sends preview times and does not retry when refresh fails', async () => {
  let post; let refreshes=0;
  const controller=createTaskComposerController({debounceMs:0,request:async(url,options)=>{
    if(url==='/tasks/preview') return {...plan(['10:00','10:07']),targets:[{name:'甲',sec_uid:'u1'},{name:'乙',sec_uid:'u2'}]};
    post=JSON.parse(options.body); return {count:2,items:[{id:'1',name:'甲',time:'10:00'},{id:'2',name:'乙',time:'10:07'}],times:post.expected_times,entries:plan(['10:00','10:07']).entries};
  },refresh:async()=>{refreshes++;return null}});
  controller.configure({accountId:'a',count:2,targets:[{name:'甲',sec_uid:'u1'},{name:'乙',sec_uid:'u2'}],csrf:'c',mode:'manual',preferred:'10:00'});
  await new Promise(resolve=>setTimeout(resolve,0));
  await controller.create({csrf:'c',payload:{account_id:'a',targets:[{name:'甲',sec_uid:'u1'},{name:'乙',sec_uid:'u2'}],message_kind:'douyin_sticker'}});
  assert.deepEqual(post.expected_times,['10:00','10:07']);
  assert.equal(refreshes,1); assert.equal(controller.getState().success.count,2);
  await controller.create({csrf:'c',payload:{account_id:'a',targets:[{name:'甲',sec_uid:'u1'},{name:'乙',sec_uid:'u2'}],message_kind:'douyin_sticker'}});
  assert.equal(refreshes,1);
});

test('optimistic task rows deduplicate ids', () => {
  assert.deepEqual(mergeTaskRows([{id:'a',name:'old'}],[{id:'b',name:'new'},{id:'a',name:'duplicate'}]).map(row=>row.id),['b','a']);
});
