import test from 'node:test';
import assert from 'node:assert/strict';
import {assertApiContract} from './api-contract-validation.js';

test('missing required account and task data fails visibly instead of becoming an empty workspace',()=>{
  const data={user:{id:'user',name:'用户名',role:'user'},csrf:'c',accounts:[],tasks:[],runs:[],membership:{},resource_limits:{},activity:{}};
  assert.equal(assertApiContract('/api/app/bootstrap',data),data);
  for(const key of ['csrf','user','accounts','tasks','runs']){
    const broken={...data};delete broken[key];
    assert.throws(()=>assertApiContract('/api/app/bootstrap',broken),error=>error.code==='API_CONTRACT_MISMATCH');
  }
});

test('new server fields stay compatible but wrong types and unknown roles are rejected',()=>{
  const data={user:{id:'user',name:'用户名',role:'user'},csrf:'c',accounts:[],tasks:[],runs:[],membership:{},resource_limits:{},activity:{},new_field:'safe'};
  assert.doesNotThrow(()=>assertApiContract('/api/app/bootstrap',data));
  assert.throws(()=>assertApiContract('/api/app/bootstrap',{...data,user:{...data.user,role:'staff'}}));
  assert.throws(()=>assertApiContract('/api/app/bootstrap',{...data,tasks:[{id:'t',accountId:'a',name:'好友',time:'09:30',enabled:'false'}]}));
});

test('probe polling never accepts an invented terminal success state',()=>{
  const probe={id:'p',task_id:'t',run_id:null,kind:'selfcheck',status:'queued',result:null,status_url:'/api/task-probes/p',created_at:'2026-11-07T00:00:00Z'};
  assert.equal(assertApiContract('/api/task-probes/p',probe),probe);
  assert.throws(()=>assertApiContract('/api/task-probes/p',{...probe,status:'success'}));
});
