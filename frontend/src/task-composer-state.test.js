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

test('automatic default keeps an empty anchor and an edited time switches to manual', async () => {
  const requests = [];
  const target = {name:'甲',sec_uid:'u1'};
  const controller = createTaskComposerController({
    debounceMs: 0,
    request: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null;
      requests.push({url, body});
      const time = body?.mode === 'manual' ? body.time : '14:10';
      return {...plan([time]), targets:[target], mode:body?.mode || 'auto'};
    },
  });

  controller.configure({accountId:'a', count:1, targets:[target], csrf:'c', mode:'auto', preferred:''});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/tasks/preview');
  assert.equal(requests[0].body.mode, 'auto');
  assert.equal(requests[0].body.time, '');
  assert.equal(controller.getState().plan.times[0], '14:10');

  // Editing the pre-filled time must send the chosen anchor to the server;
  // the server can then place later friends around that anchor.
  controller.configure({accountId:'a', count:1, targets:[target], csrf:'c', mode:'manual', preferred:'18:20'});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(requests.length, 2);
  assert.equal(requests[1].body.mode, 'manual');
  assert.equal(requests[1].body.time, '18:20');
  assert.equal(controller.getState().plan.times[0], '18:20');
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
  assert.equal(post.schedule_mode,'manual');
  assert.equal(post.send_time,'10:00');
  assert.equal(refreshes,1); assert.equal(controller.getState().success.count,2);
  await controller.create({csrf:'c',payload:{account_id:'a',targets:[{name:'甲',sec_uid:'u1'},{name:'乙',sec_uid:'u2'}],message_kind:'douyin_sticker'}});
  assert.equal(refreshes,1);
});

test('optimistic task rows deduplicate ids', () => {
  assert.deepEqual(mergeTaskRows([{id:'a',name:'old'}],[{id:'b',name:'new'},{id:'a',name:'duplicate'}]).map(row=>row.id),['b','a']);
});
