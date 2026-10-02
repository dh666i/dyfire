import test from 'node:test';
import assert from 'node:assert/strict';
import {focusTrapTarget, getMobileDockRoutes, resetRouteScroll} from './navigation.js';

const routes = [
  ['/dashboard', '首页'], ['/studio', '续火任务'], ['/accounts', '抖音账号'],
  ['/runs', '执行记录'], ['/subscription', '订阅管理'], ['/account-center', '账户中心'],
];
const adminRoutes = [
  ['/admin/overview', '业务概览'], ['/admin/users', '用户管理'],
  ['/admin/codes', '兑换码'], ['/admin/runs', '执行记录'],
  ['/admin/operations', '运维监控'], ['/admin/settings', '系统设置'],
];

test('mobile docks keep user subscription visible and expose admin operations', () => {
  assert.deepEqual(getMobileDockRoutes(routes, adminRoutes, false).map(([href]) => href), [
    '/dashboard', '/subscription', '/studio', '/accounts', '/runs',
  ]);
  assert.deepEqual(getMobileDockRoutes(routes, adminRoutes, true).map(([href]) => href), [
    '/admin/overview', '/admin/users', '/admin/codes', '/admin/runs', '/admin/operations',
  ]);
});

test('modal focus wraps at both ends and returns focus from outside the dialog', () => {
  const [first, middle, last, outside] = [{}, {}, {}, {}];
  const focusable = [first, middle, last];
  const contains = element => element !== outside;
  assert.equal(focusTrapTarget({key:'Tab', shiftKey:false, activeElement:last, focusable, contains}), first);
  assert.equal(focusTrapTarget({key:'Tab', shiftKey:true, activeElement:first, focusable, contains}), last);
  assert.equal(focusTrapTarget({key:'Tab', shiftKey:false, activeElement:outside, focusable, contains}), first);
  assert.equal(focusTrapTarget({key:'Tab', shiftKey:false, activeElement:middle, focusable, contains}), null);
  assert.equal(focusTrapTarget({key:'Escape', shiftKey:false, activeElement:last, focusable, contains}), null);
});

test('route navigation returns to the page top without overriding a hash target', () => {
  const positions = [];
  const windowObject = {scrollTo: (...position) => positions.push(position)};
  resetRouteScroll({pathname:'/subscription', hash:''}, windowObject);
  resetRouteScroll({pathname:'/studio', hash:'#task-composer'}, windowObject);
  assert.deepEqual(positions, [[0, 0]]);
});
