import test from 'node:test';
import assert from 'node:assert/strict';
import {accountCheckResult,accountNeedsRecovery} from './account-check-state.js';
const now=Date.parse('2026-11-10T12:00:00Z');
const request={state:'pending',startedAt:now,previousChecked:'2026-11-10T11:00:00Z'};
test('a queued request or old valid result cannot complete a new check',()=>{
  assert.equal(accountCheckResult({health:{state:'valid',checked_at:request.previousChecked}},request,now+1000).state,'pending');
  assert.equal(accountCheckResult({health:{state:'valid',checked_at:'2026-11-10T11:50:00Z'}},request,now+1000).state,'pending');
});
test('only explicit final evidence confirms recovery or verification',()=>{
  const health={state:'valid',checked_at:'2026-11-10T12:00:02Z'};
  assert.equal(accountCheckResult({health},request,now+3000).state,'valid');
  assert.equal(accountCheckResult({health:{...health,blocked:true}},request,now+3000).state,'pending');
  for(const state of ['verification','invalid'])assert.equal(accountCheckResult({health:{...health,state,blocked:true}},request,now+3000).state,'verification');
  assert.equal(accountNeedsRecovery({validation_state:'invalid'}),true);
});
test('queued and active server checks are never mistaken for a completed verification',()=>{
  const health={state:'temporary',blocked:true,checked_at:'2026-11-10T12:00:02Z'};
  for(const queued of [health,{...health,checking:true},{...health,next_check_at:'2026-11-10T12:00:30Z'},
    {...health,state:'valid',blocked:false,checking:true},{...health,state:'verification',checking:true},
    {...health,state:'relogin'}])assert.equal(accountCheckResult({health:queued},request,now+3000).state,'pending');
});
test('a manual refresh after polling timeout still accepts a real terminal result',()=>{
  const waiting=accountCheckResult({},request,now+61000);
  assert.equal(waiting.state,'waiting');
  assert.equal(accountCheckResult({health:{state:'valid',checked_at:'2026-11-10T12:01:05Z'}},waiting,now+66000).state,'valid');
  assert.equal(accountCheckResult({health:{state:'verification',checked_at:'2026-11-10T12:01:05Z'}},waiting,now+66000).state,'verification');
  assert.equal(accountCheckResult({health:{state:'temporary',checked_at:'2026-11-10T12:01:05Z'}},waiting,now+66000).state,'waiting');
});
test('timeout and unknown state never masquerade as successful recovery',()=>{
  assert.equal(accountCheckResult({health:{state:'unknown',checked_at:'2026-11-10T12:00:02Z'}},request,now+3000).state,'pending');
  assert.equal(accountCheckResult({},request,now+61000).state,'waiting');
  assert.equal(accountCheckResult({health:{state:'valid'}},{state:'error',message:'request failed'},now).state,'error');
});
