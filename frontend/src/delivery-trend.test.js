import test from 'node:test';
import assert from 'node:assert/strict';
import {deliverySeries,deliveryScale,deliveryDate,deliveryKeyIndex,deliveryDescription} from './delivery-trend.js';

test('zero executions are known data while absent and invalid counts remain missing',()=>{
  const series=deliverySeries([{date:'2026-11-01',total:0,success:0,failed:0,skipped:0},{date:'2026-11-02',total:null,success:0,failed:0,skipped:0},{date:'2026-11-03',total:2,success:3,failed:0,skipped:0}]);
  assert.equal(series.knownDays,1);
  assert.equal(series.missingDays,2);
  assert.equal(series.total,0);
  assert.equal(series.days[0].known,true);
  assert.equal(series.days[1].known,false);
  assert.equal(series.days[2].known,false);
  assert.match(deliveryDescription(series.days[0]),/共 0 次/);
  assert.match(deliveryDescription(series.days[1]),/尚未收到完整统计/);
});

test('missing calendar days get explicit unknown slots and numeric month labels',()=>{
  const series=deliverySeries([{date:'2026-11-03',total:5,success:2,failed:1,skipped:0},{date:'2026-11-01',total:3,success:2,failed:0,skipped:1},{date:'2026-02-30',total:0,success:0,failed:0,skipped:0}]);
  assert.deepEqual(series.days.map(day=>day.date),['2026-11-01','2026-11-02','2026-11-03']);
  assert.equal(series.days[1].total,null);
  assert.equal(series.total,8);
  assert.equal(series.other,2);
  assert.equal(series.maximum,5);
  assert.equal(deliveryDate(series.days[0].date),'11月1日');
  assert.match(deliveryDescription(series.days[2]),/其他状态 2 次/);
});

test('duplicate days are not double counted and large invalid ranges are not expanded',()=>{
  const day={date:'2026-11-01',total:2,success:2,failed:0,skipped:0};
  assert.equal(deliverySeries([day,day]).total,2);
  assert.equal(deliverySeries([{...day,date:'2000-01-01'},day]).days.length,2);
  assert.deepEqual(deliverySeries(null).days,[]);
});

test('chart bounds include all outcomes and keyboard selection stops at visible ends',()=>{
  assert.equal(deliveryScale(0),2);
  assert.equal(deliveryScale(17),20);
  assert.equal(deliveryScale(101),200);
  assert.equal(deliveryKeyIndex(7,null,'ArrowLeft'),6);
  assert.equal(deliveryKeyIndex(7,0,'ArrowLeft'),0);
  assert.equal(deliveryKeyIndex(7,6,'ArrowRight'),6);
  assert.equal(deliveryKeyIndex(7,3,'Home'),0);
  assert.equal(deliveryKeyIndex(7,3,'End'),6);
  assert.equal(deliveryKeyIndex(0,null,'Home'),null);
});
