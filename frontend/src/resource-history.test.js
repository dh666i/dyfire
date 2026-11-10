import test from 'node:test';
import assert from 'node:assert/strict';
import {metricValue,resourceSeries,resourcePath,resourceWindow,resourceAvailability,resourceSampleAt,resourceKeyTime,formatResourcePercent,formatResourceTime} from './resource-history.js';

test('missing metric stays unknown and is excluded from aggregates',()=>{
  const result=resourceSeries({points:[{at:0,cpu:10},{at:120,cpu:90},{at:240,cpu:null}]},'cpu');
  assert.equal(result.current,null);assert.equal(result.peak,90);assert.equal(result.average,50);
});
test('history path breaks at missing readings and gaps rather than inventing a trend',()=>{
  const path=resourcePath([{at:0,cpu:20},{at:120,cpu:80},{at:240,cpu:null},{at:360,cpu:30},{at:1200,cpu:40}],'cpu',0,1200,240);
  assert.equal(path,'M0.00,80.00 L60.00,20.00  M180.00,70.00 M600.00,60.00');
});

test('zero is a real reading while whitespace, booleans and malformed timestamps are unknown',()=>{
  for(const value of [null,undefined,' ',false,{},'NaN']) assert.equal(metricValue(value),null);
  assert.equal(metricValue('0'),0);
  assert.equal(formatResourcePercent(0),'0%');
  assert.equal(formatResourcePercent(0.4),'0.4%');
  assert.equal(formatResourcePercent(null),'未采集');
  const result=resourceSeries({points:[null,{at:null,cpu:100},{at:'',cpu:80},{at:0,cpu:0},{at:'120',cpu:0.4}]},'cpu');
  assert.deepEqual(result.points.map(point=>point.at),[0,120]);
  assert.equal(result.validCount,2);
});

test('time range uses the server clock, preserves resolution and excludes outside points',()=>{
  const history={start:0,end:86400,bucket_seconds:120,points:[{at:120,cpu:10},{at:82799,cpu:50},{at:82800,cpu:0},{at:86400,cpu:12},{at:86520,cpu:99}]};
  const oneHour=resourceWindow(history,1);
  assert.equal(oneHour.start,82800);
  assert.equal(oneHour.end,86400);
  assert.equal(oneHour.bucket_seconds,120);
  assert.deepEqual(oneHour.points.map(point=>point.at),[82800,86400]);
  assert.equal(resourceSeries(oneHour,'cpu').peak,12);
  assert.equal(resourceWindow({...history,start:85000},6).start,85000);
  assert.equal(history.points.length,5);
});

test('inspection does not stretch an old sample into an unsampled interval',()=>{
  const points=[{at:0,cpu:0},{at:240,cpu:90},{at:360,cpu:null}];
  assert.equal(resourceSampleAt(points,119,120)?.cpu,0);
  assert.equal(resourceSampleAt(points,120,120),null);
  assert.equal(resourceSampleAt(points,239,120),null);
  assert.equal(resourceSampleAt(points,240,120)?.cpu,90);
  assert.equal(resourceSampleAt(points,-1,120),null);
  assert.equal(resourceSampleAt(points,480,120),null);
  assert.equal(resourceSampleAt(points,360,120)?.cpu,null);
});

test('keyboard inspection follows timestamps through gaps and remains at range ends',()=>{
  const points=[{at:0},{at:120},{at:600}];
  assert.equal(resourceKeyTime(points,null,'ArrowLeft'),600);
  assert.equal(resourceKeyTime(points,600,'ArrowLeft'),120);
  assert.equal(resourceKeyTime(points,300,'ArrowLeft'),120);
  assert.equal(resourceKeyTime(points,300,'ArrowRight'),600);
  assert.equal(resourceKeyTime(points,0,'ArrowLeft'),0);
  assert.equal(resourceKeyTime(points,600,'ArrowRight'),600);
  assert.equal(resourceKeyTime(points,120,'Home'),0);
  assert.equal(resourceKeyTime(points,120,'End'),600);
  assert.equal(resourceKeyTime([],null,'Home'),null);
});

test('availability distinguishes loading, first collection, failed reads, missing metrics and valid zero',()=>{
  assert.equal(resourceAvailability(null,'cpu').state,'loading');
  assert.equal(resourceAvailability({state:'empty',points:[]},'cpu').state,'empty');
  const valid={state:'fresh',points:[{at:0,cpu:0}]};
  assert.equal(resourceAvailability(valid,'cpu').state,'fresh');
  assert.equal(resourceAvailability({...valid,state:'unavailable'},'cpu').state,'error');
  assert.equal(resourceAvailability({...valid,state:'stale'},'cpu').state,'stale');
  assert.equal(resourceAvailability(valid,'disk').state,'missing');
  assert.equal(resourceAvailability({...valid,state:'partial'},'cpu').state,'partial');
});

test('formatted dates use Shanghai midnight and numeric Chinese month/day',()=>{
  const at=Date.parse('2026-11-09T16:00:00Z')/1000;
  assert.equal(formatResourceTime(at),'11月10日 00:00');
  assert.equal(formatResourceTime(at,{date:false}),'00:00');
  assert.equal(formatResourceTime(null),'—');
});
