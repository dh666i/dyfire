import test from 'node:test';
import assert from 'node:assert/strict';
import {filterStreakContacts,streakHistoryPath,streakMilestones,streakSnapshot,streakStateLabel,streakSummary,streakTime} from './spark-streak-state.js';

const observed_at='2026-11-11T01:30:00Z';
const snapshot=(days,state='active',extra={})=>({days,state,observed_at,source:'douyin_dom',...extra});

test('missing observations stay unknown instead of becoming a zero-day or successful streak',()=>{
  for(const value of [undefined,null,{}, {days:null}, {days:90,state:'active'}, {days:90,state:'active',observed_at:'invalid'}]) {
    assert.equal(streakSnapshot(value).days,null);
    assert.equal(streakSnapshot(value).state,'unknown');
    assert.match(streakSummary(value),/天数待同步/);
  }
  assert.equal(streakSnapshot(snapshot(0,'broken')).days,0);
  for(const days of ['', '90', -1, 1.2, NaN, Infinity]) assert.equal(streakSnapshot(snapshot(days)).days,null);
});

test('stale evidence is labeled as past state and excluded from currently active filter',()=>{
  const old=snapshot(58,'active',{stale:true});
  assert.equal(streakStateLabel(old),'上次已点亮');
  const items=[{name:'older',spark:old},{name:'fresh',spark:snapshot(5)}];
  assert.deepEqual(filterStreakContacts(items,{filter:'active'}).map(x=>x.name),['fresh']);
  assert.equal(filterStreakContacts(items,{filter:'has_streak'}).length,2);
});

test('friend filter retains unknown values last in day sort and matches duplicate protection identity',()=>{
  const items=[{name:'未读',sec_uid:'unknown'}, {name:'同名',sec_uid:'existing',spark:snapshot(7)}, {name:'同名',sec_uid:'new',spark:snapshot(190)}, {name:'恢复',remark_name:'好友',sec_uid:'recover',spark:snapshot(39,'recovering')}, {name:'熄灭',sec_uid:'broken',spark:snapshot(0,'broken')}];
  assert.deepEqual(filterStreakContacts(items,{order:'days_desc'}).map(x=>x.sec_uid),['new','recover','existing','broken','unknown']);
  assert.deepEqual(filterStreakContacts(items,{query:'同名',filter:'without_task',existing:new Set(['existing'])}).map(x=>x.sec_uid),['new']);
  assert.deepEqual(filterStreakContacts(items,{filter:'needs_attention'}).map(x=>x.sec_uid),['recover','broken']);
  assert.equal(filterStreakContacts(items,{query:' 好友 '})[0].name,'恢复');
  assert.equal(items[0].name,'未读');
});

test('details require stable account and target identity, never fall back to display name',()=>{
  assert.equal(streakHistoryPath({accountId:'a',name:'朋友'}),null);
  assert.equal(streakHistoryPath({sec_uid:'sec'}),null);
  const path=streakHistoryPath({accountId:'account / 1',sec_uid:'sec&other'},30);
  const params=new URL(path,'http://localhost').searchParams;
  assert.equal(params.get('account_id'),'account / 1');
  assert.equal(params.get('sec_uid'),'sec&other');
  assert.equal(params.get('offset'),'30');
});

test('milestones only show real dated observations, and timestamps use Beijing time',()=>{
  const evidence={days:100,kind:'first_observed',observed_at};
  assert.deepEqual(streakMilestones([evidence,{days:200,kind:'crossed'}, {days:300,kind:'predicted',observed_at}, {days:-1,kind:'crossed',observed_at}]),[evidence]);
  assert.match(streakTime(observed_at),/09:30/);
  assert.equal(streakTime('2026-11-11T01:30:00'),streakTime(observed_at));
  assert.equal(streakTime(null),'尚未同步');
});
