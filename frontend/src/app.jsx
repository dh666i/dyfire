import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {BrowserRouter, Link, useLocation, useNavigate} from 'react-router-dom';
import {focusTrapTarget, getMobileDockRoutes, resetRouteScroll} from './navigation.js';
import {contactKey, createContactPickerController} from './contact-picker-state.js';
import {createTaskComposerController, mergeTaskRows} from './task-composer-state.js';
// Lightweight motion primitives — avoids motion/react subpath import issues with Vite 8 Rolldown
function AnimatePresence({children}) { return children; }
function RouteMotionDiv({children, locationKey}) {
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => { setVisible(false); const t = setTimeout(() => setVisible(true), 10); return () => clearTimeout(t); }, [locationKey]);
  return <div className="route-view" style={{opacity: visible?1:0, transform: visible?'none':'translateY(5px)', transition: 'opacity 150ms ease-out, transform 150ms ease-out'}}>{children}</div>;
}
// eslint-disable-next-line no-unused-vars
const _motion_react_unused = null; // motion/react used by RubberSegment.jsx
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import './react.css';
import './react-polish.css';
import './react-fire.css';
import RubberSegment from './components/RubberSegment';
import {cloudFrameOverlay, createCloudFrameState, frameLoadError, frameLoaded, retryCloudFrame} from './cloud-frame-state.js';

const asList = value => Array.isArray(value) ? value.filter(Boolean) : [];
const displayText = (value, fallback='') => typeof value === 'string' && value.trim() ? value : fallback;
const displayName = (value, fallback='抖音账号') => displayText(value, fallback);
const initial = (value, fallback='·') => displayName(value, fallback).slice(0, 1);
const redemptionPlaintext = code => typeof code === 'string' ? code : displayText(code?.code || code?.full_code || code?.plaintext || code?.code_plaintext || code?.value, '');
const formatAdminDate = value => value ? new Date(value).toLocaleString('zh-CN', {dateStyle:'medium', timeStyle:'short'}) : '—';
const adminUserLabel = user => user?.admin_note ? `${user.username}（${user.admin_note}）` : user?.username || '';
const interactionRequestId = () => globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r=Math.random()*16|0; return (c==='x'?r:(r&3|8)).toString(16); });
const pendingMembershipPlan = membership => {
  const candidate = membership?.pending_subscription || membership?.pending_plan || membership?.next_subscription || membership?.next_plan;
  return candidate && typeof candidate === 'object' ? candidate : null;
};
const membershipMaxTasks = value => {
  const raw = value?.max_tasks ?? value?.task_limit ?? value?.tasks_limit;
  return raw == null || raw === '' ? null : (Number.isFinite(Number(raw)) ? Number(raw) : null);
};
const membershipTaskLimitLabel = value => value?.task_limit_label || (membershipMaxTasks(value) == null ? '不限' : (membershipMaxTasks(value) >= 2147483647 ? '不限' : `${membershipMaxTasks(value)} 个好友任务`));
const membershipEnabledTaskUsage = value => value?.enabled_task_count ?? value?.active_task_count ?? null;
const membershipTaskUsage = value => membershipEnabledTaskUsage(value) ?? value?.task_count ?? null;
const membershipOverLimit = value => {
  const explicit = value?.over_limit_task_count ?? value?.over_limit_enabled_tasks ?? value?.task_overage ?? value?.task_over_limit;
  if (Number.isFinite(Number(explicit))) return Math.max(0, Number(explicit));
  const usage = membershipEnabledTaskUsage(value); const limit = membershipMaxTasks(value);
  return Number.isFinite(Number(usage)) && Number.isFinite(limit) && limit < 2147483647 ? Math.max(0, Number(usage) - limit) : 0;
};
const membershipDate = value => value ? new Date(value).toLocaleDateString('zh-CN', {year:'numeric', month:'numeric', day:'numeric'}) : '—';
const pendingPlanIsDowngrade = (membership, pending) => {
  const current = membershipMaxTasks(membership); const next = membershipMaxTasks(pending);
  return Number.isFinite(current) && Number.isFinite(next) && next < current;
};
function normalizeBootstrap(value) {
  const source = value && typeof value === 'object' ? value : {};
  const mail = source.mail && typeof source.mail === 'object' ? source.mail : {};
  return {
    ...source,
    user: source.user && typeof source.user === 'object' ? source.user : {},
    accounts: asList(source.accounts),
    tasks: asList(source.tasks),
    runs: asList(source.runs),
    recentRuns: asList(source.recentRuns),
    users: asList(source.users),
    subscriptions: asList(source.subscriptions),
    redemptionCodes: asList(source.redemptionCodes),
    groups: asList(source.groups),
    audit: asList(source.audit),
    activity: source.activity && typeof source.activity === 'object' ? source.activity : {days: [], total: 0, period_days: 365},
    membership: source.membership && typeof source.membership === 'object' ? source.membership : {},
    onboarding: source.onboarding && typeof source.onboarding === 'object' ? source.onboarding : {},
    plans: asList(source.plans),
    operations: source.operations && typeof source.operations === 'object' ? source.operations : {},
    mail: {...mail, events: asList(mail.events), deliveries: asList(mail.deliveries)},
  };
}

const _si = (d, extra={}) => (
  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" {...extra}>
    {d}
  </svg>
);
const NavIconHome       = () => _si(<><path d="M3 9.5 12 3l9 6.5V20a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21V12h6v9"/></>);
const NavIconFlame      = () => _si(<path d="M12 4c0 0-5 4.5-5 10a5 5 0 0 0 10 0c0-2-.7-3.8-2.5-5.5 0 0 .5 3.5-2 5-1.2-3.5-1-7 .5-9.5z"/>);
const NavIconPerson     = () => _si(<><circle cx="12" cy="7" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></>);
const NavIconHistory    = () => _si(<><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></>);
const NavIconCard       = () => _si(<><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></>);
const NavIconUserCircle = () => _si(<><circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.2 19.4a6 6 0 0 1 11.6 0"/></>);
const NavIconBarChart   = () => _si(<><rect x="3" y="12" width="4" height="9"/><rect x="10" y="7" width="4" height="14"/><rect x="17" y="3" width="4" height="18"/></>);
const NavIconUsers      = () => _si(<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></>);
const NavIconTag        = () => _si(<path d="M20.6 11.4 12.4 3.2A1 1 0 0 0 11.7 3H4a1 1 0 0 0-1 1v7.7a1 1 0 0 0 .3.7l8.2 8.2a1 1 0 0 0 1.4 0l7.7-7.7a1 1 0 0 0 0-1.5zM7 8a1 1 0 1 1 0-2 1 1 0 0 1 0 2z"/>);
const NavIconPulse      = () => _si(<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>);
const NavIconSliders    = () => _si(<><line x1="4" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="12" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="12" cy="18" r="2"/></>);
// Task-row actions share the same SVG helper so they remain defined in both
// the normal app bundle and the lightweight preview/runtime build.
const ActionEditIcon   = () => _si(<><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></>);
const ActionPauseIcon  = () => _si(<><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></>);
const ActionPlayIcon   = () => _si(<path d="m8 5 11 7-11 7Z"/>);
const ActionDeleteIcon = () => _si(<><path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/></>);
const routes = [
  ['/dashboard','首页',NavIconHome], ['/studio','续火任务',NavIconFlame], ['/accounts','抖音账号',NavIconPerson],
  ['/runs','执行记录',NavIconHistory], ['/subscription','订阅管理',NavIconCard], ['/account-center','账户中心',NavIconUserCircle],
];
const adminRoutes = [
  ['/admin/overview','业务概览',NavIconBarChart], ['/admin/users','用户管理',NavIconUsers],
  ['/admin/codes','兑换码',NavIconTag], ['/admin/runs','执行记录',NavIconHistory],
  ['/admin/operations','运维监控',NavIconPulse], ['/admin/settings','系统设置',NavIconSliders],
];

const previewRequested = import.meta.env.DEV && new URLSearchParams(window.location.search).has('preview');
let previewScan = null;
let previewCreatedTasks = [];
let previewAttention = [{id:'preview-attention-1',title:'发送待确认 · 小林',detail:'请先核对抖音聊天记录，不要直接重发。',url:'/runs',at:'2026-09-28T09:45:00+08:00',tone:'warning',read:false}];

function previewData() {
  const account = {id:'preview-account', name:'主账号', profile:{nickname:'火花用户'}, health:{state:'valid', label:'登录有效'}, validation_state:'valid'};
  const activityDays = Array.from({length:365},(_,i)=>{const date=new Date();date.setHours(12,0,0,0);date.setDate(date.getDate()-(364-i));return {date:date.toISOString().slice(0,10),count:(i*7)%6};});
  const secondAccount = {id:'preview-account-2', name:'副账号', profile:{nickname:'备用火花'}, health:{state:'verification', label:'需要重新验证'}, validation_state:'verification'};
  const thirdAccount = {id:'preview-account-3', name:'测试账号', profile:{nickname:'小号'}, health:{state:'valid', label:'登录有效'}, validation_state:'valid'};
  const tasks = [
    {id:'preview-task-1', accountId:account.id, name:'小琪', sec_uid:'preview-friend-1', avatar:'', kind:'续火花表情', time:'09:30', enabled:true},
    {id:'preview-task-2', accountId:account.id, name:'阿杰', sec_uid:'preview-friend-2', avatar:'', kind:'续火花表情', time:'12:20', enabled:true},
    {id:'preview-task-3', accountId:account.id, name:'郭琪', sec_uid:'preview-friend-3', avatar:'', kind:'文字消息', time:'18:00', enabled:false, message:'今天也要记得续上火花。'},
    {id:'preview-task-4', accountId:secondAccount.id, name:'小林', sec_uid:'preview-friend-4', avatar:'', kind:'续火花表情', time:'09:45', enabled:true},
    {id:'preview-task-5', accountId:thirdAccount.id, name:'小雨', sec_uid:'preview-friend-5', avatar:'', kind:'续火花表情', time:'14:20', enabled:true},
    {id:'preview-task-6', accountId:thirdAccount.id, name:'阿南', sec_uid:'preview-friend-6', avatar:'', kind:'文字消息', time:'19:00', enabled:true, message:'今天也要记得续上火花。'},
  ];
  const runs = [
    {id:'preview-run-1', name:'小琪', time:'今天 09:30', kind:'续火花表情', stage:'消息确认', status:'success', label:'已发出'},
    {id:'preview-run-2', name:'阿杰', time:'今天 12:20', kind:'续火花表情', stage:'消息确认', status:'success', label:'已发出'},
    {id:'preview-run-3', name:'郭琪', time:'今天 08:50', kind:'文字消息', stage:'账号复核', status:'failed', label:'未发出', error:'账号需要重新确认登录', guidance:{text:'请到抖音账号页完成验证。'}},
    {id:'preview-run-4', name:'小林', time:'今天 09:45', kind:'续火花表情', stage:'账号复核', status:'submitted_unconfirmed', label:'待核实', error:'消息已进入发送步骤，暂未取得页面确认', attempts:[{number:1,stage:'消息确认',reason:'页面未返回确认信号'}]},
    {id:'preview-run-5', name:'小雨', time:'今天 14:20', kind:'续火花表情', stage:'执行中', status:'running', label:'执行中'},
    {id:'preview-run-6', name:'阿南', time:'昨天 19:00', kind:'文字消息', stage:'消息确认', status:'success', label:'已发出'},
  ];
  return {
    user:{name:'演示用户', role:new URLSearchParams(window.location.search).has('admin')?'admin':'user'}, csrf:'preview-csrf',
    accounts:[account,secondAccount,thirdAccount], tasks:[...previewCreatedTasks,...tasks], runs, recentRuns:runs.map((run,index)=>({id:run.id,target:run.name,owner:'演示用户',time:run.time,status:run.label,tone:run.status==='success'?'good':run.status==='running'?'warn':'danger'})),
    membership:{active:true, can_add_task:true, max_accounts:1, max_tasks:10, task_limit_label:'10 个好友任务', task_count:10, enabled_task_count:8, task_overage:0, days_remaining:27, valid_until:'2026-10-30T00:00:00+08:00', status_label:'标准版 · 使用中', pending_subscription:{valid_from:'2026-10-30T00:00:00+08:00',valid_until:'2026-11-29T00:00:00+08:00',max_accounts:1,max_tasks:5,task_limit_label:'5 个好友任务',status_label:'基础版'}},
    activity:{period_days:365,total:126,days:activityDays},
    counts:{users:18, accounts:26, tasks:74, runs:1260},
    users:[{id:'preview-user-1',username:'演示用户',role:'user',status:'active',created:'2026-09-01',account_count:1,task_count:3,membership_label:'有效订阅',account_health_status:'需处理',account_health_label:'1 个账号需复核',today_success:2,today_failed:1},{id:'preview-user-2',username:'管理员',role:'admin',status:'active',created:'2026-08-21',account_count:2,task_count:8,membership_label:'管理员',account_health_status:'正常',account_health_label:'账号正常',today_success:8,today_failed:0}],
    subscriptions:[{id:'preview-user-1',username:'演示用户',account_count:1,task_count:3,membership_status:'active',membership_label:'有效',valid_until:'2026-10-18',max_accounts:2,max_tasks:10}],
    redemptionCodes:[{id:'preview-code-1',code:'DYFIRE-DEMO-30-ABCD',status:'待兑换',redeemed_at:null,expires_at:null,max_accounts:1,max_tasks:10,task_limit_label:'10'}],
    groups:[{owner_id:'preview-user-1',account_id:account.id,username:'演示用户',remark:'我的抖音账号',nickname:'火花用户',total:3,enabled:2,validation_state:'valid'}],
    operations:{refresh_interval_seconds:30,system:{scope_label:'本地预览服务器',cpu:{percent:18},memory:{percent:42},disk:{percent:37}},services:{worker:{status:'运行中',label:'运行中'}}},
    audit:[{id:'preview-audit-1',event:'任务发送',subject_type:'task_run',created_at:'2026-09-21T09:30:00+08:00',detail:'小琪 · 已发出'}],
    mail:{enabled:false,host:'',port:465,mode:'ssl',username:'',sender:'',sender_name:'火花守护',password_configured:false,events:[],deliveries:[]},
  };
}

function mockApi(path, options={}) {
  const data = previewData();
  if (path.startsWith('/api/studio/schedule?')) {
    const query=new URLSearchParams(path.split('?')[1]);
    const count=Math.max(1,Number(query.get('count'))||1), mode=query.get('mode')||'auto';
    const preferred=mode==='manual'?query.get('preferred'):'14:10';
    const [hour,minute]=String(preferred).split(':').map(Number);
    const times=Array.from({length:count},(_,index)=>{const minutes=(hour*60+minute+index*7)%1440;return `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`});
    const entries=times.map((time,index)=>{const date=new Date();const [h,m]=time.split(':').map(Number);date.setHours(h,m,0,0);if(date<=new Date())date.setDate(date.getDate()+1);return {index,time,next_run_at:date.toISOString(),day_offset:date.getDate()===new Date().getDate()?0:1}});
    return Promise.resolve({status:'ready',mode,times,entries,suggested:times[0],gap_minutes:5,reason:'已为每位好友安排发送时间。',timezone:'Asia/Shanghai'});
  }
  if (path==='/tasks/preview' && options.method==='POST') {
    const body=JSON.parse(options.body||'{}'); const targets=Array.isArray(body.targets)?body.targets:[];
    const preferred=body.time||'14:10'; const [hour,minute]=preferred.split(':').map(Number);
    const times=targets.map((_,index)=>{const total=(hour*60+minute+index*7)%1440;return `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`});
    const entries=times.map((time,index)=>({index,time,next_run_at:new Date(Date.now()+86400000+index*420000).toISOString(),day_offset:1}));
    return Promise.resolve({status:'ready',mode:body.mode||'auto',times,entries,targets,count:targets.length,gap_minutes:5,reason:'已为每位好友安排发送时间。'});
  }
  if (path==='/api/studio/tasks'&&options.method==='POST') {
    const body=JSON.parse(options.body||'{}');
    const items=body.targets.map((friend,index)=>({id:`preview-created-${interactionRequestId()}`,accountId:body.account_id,name:friend.name,sec_uid:friend.sec_uid,time:body.expected_times[index],kind:body.message_kind==='text'?'文字消息':'续火花表情',enabled:true,confirmed:false,status:'scheduled'}));
    previewCreatedTasks=[...items,...previewCreatedTasks];
    return Promise.resolve({items,count:items.length,times:body.expected_times,entries:body.expected_times.map((time,index)=>({time,next_run_at:body.expected_next_runs[index]}))});
  }
  if (path === '/api/app/bootstrap' || path === '/api/admin/bootstrap') return Promise.resolve(data);
  if (path.startsWith('/api/admin/redemption-codes')) return Promise.resolve({items:data.redemptionCodes,pagination:{page:1,page_size:20,total:data.redemptionCodes.length,pages:1,has_previous:false,has_prev:false,has_next:false}});
  if (path === '/api/notifications') return Promise.resolve({csrf:data.csrf,mail_ready:true, preference:{email:'',enabled:false,verified:false}, policy:{events:['account_alert','account_recovered','task_failed','expiry'],quiet_start:23,quiet_end:8,quiet_enabled:false,interval_minutes:60}});
  if (path === '/api/attention') return Promise.resolve({csrf:data.csrf,items:previewAttention});
  if (path === '/api/account-center') return Promise.resolve({csrf:data.csrf,current_session_id:'preview-session-1',sessions:[{id:'preview-session-1',created_at:'2026-09-28T08:00:00+08:00',expires_at:'2026-09-28T16:00:00+08:00',current:true},{id:'preview-session-2',created_at:'2026-09-26T19:00:00+08:00',expires_at:'2026-09-27T03:00:00+08:00',current:false}]});
  if (path === '/api/subscription') return Promise.resolve({csrf:data.csrf,membership:{...data.membership,valid_until:'2026-10-25T00:00:00+08:00',account_count:3},grants:[{id:'preview-grant-1',source:'redemption_code',valid_from:'2026-08-28T00:00:00+08:00',valid_until:'2026-10-25T00:00:00+08:00',created_at:'2026-08-28T00:00:00+08:00'}]});
  if (path === '/api/attention/read' && options.method === 'POST') { const body=JSON.parse(options.body||'{}');previewAttention=previewAttention.map(item=>body.item_id==='all'||item.id===body.item_id?{...item,read:true}:item);return Promise.resolve({ok:true,items:previewAttention}); }
  if (path === '/api/account-center/revoke-sessions' && options.method === 'POST') return Promise.resolve({ok:true,message:'其他登录会话已退出。'});
  if (path === '/api/subscription/redeem' && options.method === 'POST') { const body=JSON.parse(options.body||'{}');if(!body.code)return Promise.resolve({ok:false,message:'请输入兑换码'});return Promise.resolve({ok:true,message:'预览模式：兑换操作已模拟'}); }
  if (path === '/notifications/verify-send' || path === '/notifications/verify' || path === '/notifications/save' || path === '/notifications/policy') return Promise.resolve({message:path.endsWith('verify-send')?'验证邮件已排队，请在 10 分钟内填写验证码。':path.endsWith('/verify')?'邮箱验证成功，已开启提醒。':'设置已保存'});
  if (path === '/api/admin/updates') return Promise.resolve({configured:true,current:'0.2.89',online:true,available:null,update_available:false,rollback_versions:[]});
  if (path === '/api/admin/diagnostics') return Promise.resolve({items:data.runs.map((run,index)=>({id:run.id,name:run.name,status:run.status,stage:run.stage,attempt:1,total:index===2?18:7,error_summary:run.error||''}))});
  if (path.startsWith('/admin/operations/history')) return Promise.resolve({hours:24,state:'fresh',points:Array.from({length:24},(_,i)=>({at:i,cpu:18+(i%5)*3,memory:42+(i%4)*2,disk:37,load:0.2,logins:0,sends:0})),cpu_peak:30,memory_peak:48});
  if (path.startsWith('/api/admin/tasks/groups/')) return Promise.resolve({items:data.tasks.map(task=>({...task,account:'我的抖音账号',username:'演示用户'}))});
  if (path.includes('/conversations')) return Promise.resolve({items:[
    {name:'小琪',nickname:'小琪',remark_name:'工作伙伴',sec_uid:'preview-friend-1'},
    {name:'阿杰',nickname:'阿杰',remark_name:'',sec_uid:'preview-friend-2'},
    {name:'郭琪',nickname:'郭琪',remark_name:'郭琪',sec_uid:'preview-friend-3'},
    {name:'小林',nickname:'林林',remark_name:'',sec_uid:'preview-friend-4'},
    {name:'小雨',nickname:'小雨',remark_name:'同事',sec_uid:'preview-friend-5'},
    {name:'阿南',nickname:'阿南',remark_name:'',sec_uid:'preview-friend-6'},
  ]});
  if (path === '/api/studio/runs') return Promise.resolve({items:data.runs});
  if (path === '/accounts/scan') { previewScan={id:'preview-scan',status:'waiting',message:'请使用抖音 App 扫码并在手机确认'}; return Promise.resolve(previewScan); }
  if (path.startsWith('/accounts/scan/preview-scan/qr')) return Promise.reject(new Error('预览模式不提供真实二维码'));
  if (path.startsWith('/accounts/scan/preview-scan')) return Promise.resolve(previewScan||{id:'preview-scan',status:'waiting',message:'预览登录流程'});
  if (path.startsWith('/admin/settings/mail/')) return Promise.resolve({message:'预览模式：设置未提交到服务器'});
  return Promise.resolve({ok:true, message:'预览模式：操作已模拟'});
}

async function fetchWithTimeout(path, options={}, timeoutMs) {
  // A stalled proxy or backend must resolve into an actionable UI error. The
  // caller's AbortSignal is still honoured so route changes can cancel stale
  // reads without turning them into timeout messages.
  if (typeof AbortController !== 'function') return fetch(path, options);
  const externalSignal = options.signal;
  const controller = new AbortController();
  let timedOut = false;
  const forwardAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener('abort', forwardAbort, {once:true});
  }
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await fetch(path, {...options, signal:controller.signal});
  } catch (error) {
    if (timedOut) {
      const timeoutError = new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请检查网络后重试`);
      timeoutError.code = 'TIMEOUT';
      timeoutError.status = 408;
      throw timeoutError;
    }
    if (error?.name === 'AbortError') throw error;
    const networkError = new Error('网络连接失败，请检查网络后重试');
    networkError.code = 'NETWORK_ERROR';
    networkError.cause = error;
    throw networkError;
  } finally {
    window.clearTimeout(timer);
    externalSignal?.removeEventListener?.('abort', forwardAbort);
  }
}

async function api(path, options={}) {
  if (previewRequested) return mockApi(path, options);
  const {timeoutMs: requestedTimeout, ...fetchOptions} = options;
  const method = String(fetchOptions.method || 'GET').toUpperCase();
  const requestTimeout = Number.isFinite(Number(requestedTimeout))
    ? Math.max(1000, Number(requestedTimeout))
    : (method === 'GET' ? 15000 : 25000);
  let response;
  try {
    response = await fetchWithTimeout(path, {credentials:'same-origin', cache:'no-store', ...fetchOptions}, requestTimeout);
  } catch (error) {
    // Vite's local proxy commonly has no backend during UI-only development;
    // preserve that existing preview fallback while surfacing real timeouts.
    if (import.meta.env.DEV && error?.code !== 'TIMEOUT' && error?.name !== 'AbortError') return mockApi(path, options);
    throw error;
  }
  if (response.status === 401) { window.location.assign('/login'); throw new Error('登录状态已过期'); }
  const type = response.headers?.get?.('content-type') || '';
  const value = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    // Vite returns a 5xx response when its local API proxy cannot reach the
    // backend. Keep the dev preview usable without changing production errors.
    if (import.meta.env.DEV && response.status >= 500) return mockApi(path, options);
    const error = new Error(typeof value?.detail === 'string' ? value.detail : value?.detail?.message || value?.message || '请求未完成');
    error.status = response.status;
    error.payload = value;
    throw error;
  }
  return value;
}

// ── Utility hooks ────────────────────────────────────────────────────────────

// CountUp: animates a number from 0 to `to` on mount, respects reduced-motion
function useCountUp(to, {duration=900, enabled=true}={}) {
  const [value, setValue] = useState(0);
  useEffect(() => {
    if (!enabled || typeof to !== 'number' || isNaN(to)) { setValue(to); return; }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) { setValue(to); return; }
    const start = performance.now();
    let raf;
    const tick = now => {
      const p = Math.min((now - start) / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(eased * to));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, duration, enabled]);
  return value;
}

function useDebounced(value, delay=220) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

// SplitText: renders text with each character staggered in on mount
function SplitText({children, className='', stagger=18, delay=0}) {
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return <span className={className}>{children}</span>;
  return (
    <span className={className} aria-label={children}>
      {String(children).split('').map((ch, i) => (
        <span
          key={i}
          aria-hidden="true"
          className="split-char"
          style={{display: ch === ' ' ? 'inline' : 'inline-block', animationDelay: `${delay + i * stagger}ms`}}
        >{ch}</span>
      ))}
    </span>
  );
}

// MagneticButton: wraps a button so it gently follows the cursor (desktop only)
function MagneticButton({children, className='', strength=10, ...props}) {
  const ref = useRef(null);
  const x = useRef(0);
  const y = useRef(0);
  const onMove = e => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    x.current = ((e.clientX - rect.left) / rect.width - 0.5) * strength * 2;
    y.current = ((e.clientY - rect.top) / rect.height - 0.5) * strength * 2;
    ref.current.style.transform = `translate(${x.current}px,${y.current}px)`;
  };
  const onLeave = () => {
    if (!ref.current) return;
    ref.current.style.transition = 'transform 0.4s cubic-bezier(0.23,1,0.32,1)';
    ref.current.style.transform = 'translate(0px,0px)';
    setTimeout(() => { if (ref.current) ref.current.style.transition = ''; }, 400);
  };
  return (
    <button
      ref={ref}
      className={`button ${className}`}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      {...props}
    >{children}</button>
  );
}

function Icon({children}) { return <span className="icon" aria-hidden="true">{children}</span>; }
function BellIcon() { return <svg className="bell-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>; }
function ThemeIcon({light=false}) { return light ? <svg className="theme-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg> : <svg className="theme-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.4 15.2A8.5 8.5 0 0 1 8.8 3.6 8.6 8.6 0 1 0 20.4 15.2Z" fill="currentColor"/></svg>; }
function Logo() {
  const [missing, setMissing] = useState(false);
  return <Link className="brand" to="/dashboard" aria-label="火花首页"><span className="brand-mark">{missing?'✦':<img src="/static/spark-flame.png" alt="" onError={()=>setMissing(true)} />}</span><span className="brand-copy"><b>火花</b><small>抖音自动续火花服务</small></span></Link>;
}
function Button({children, variant='', type='button', className='', ...props}) { return <button type={type} className={`button ${variant} ${className}`.trim()} {...props}>{children}</button>; }
function Badge({children, tone='neutral'}) { return <span className={`badge ${tone}`}><i />{children}</span>; }
// Compatibility wrapper for the styled native select overlay.
function Select({value, onChange, children, className='', ...props}) {
  return <select className={className} value={value} onChange={onChange} {...props}>{children}</select>;
}
// Keep native selects as the form source of truth, but render their popup in
// the workspace visual language. This avoids browser-specific grey option
// sheets while preserving React's normal value/change contract.
function installSelectOverlay() {
  if (window.__dyfireSelectOverlay) return window.__dyfireSelectOverlay;
  let active = null; let menu = null; let activeIndex = -1;
  const close = () => { if (menu) menu.remove(); menu=null; if (active) { active.removeAttribute('aria-expanded'); active=null; } activeIndex=-1; };
  const choose = index => { if (!active) return; const option=active.options[index]; if (!option || option.disabled) return; active.selectedIndex=index; active.dispatchEvent(new Event('input',{bubbles:true})); active.dispatchEvent(new Event('change',{bubbles:true})); close(); active.focus(); };
  const open = select => {
    if (select.disabled) return; close(); active=select; select.focus(); const rect=select.getBoundingClientRect();
    menu=document.createElement('div'); menu.className='fire-select-menu'; menu.setAttribute('role','listbox'); menu.setAttribute('aria-label',select.getAttribute('aria-label')||select.labels?.[0]?.textContent?.trim()||'选择');
    const options=[...select.options]; activeIndex=select.selectedIndex;
    options.forEach((option,index)=>{const row=document.createElement('button'); row.type='button'; row.className='fire-select-option'; row.setAttribute('role','option'); row.setAttribute('aria-selected',String(option.selected)); row.disabled=option.disabled; const label=document.createElement('span'); label.textContent=option.textContent; row.append(label); if(option.selected){const check=document.createElement('b'); check.setAttribute('aria-hidden','true'); check.textContent='✓'; row.append(check);} row.addEventListener('pointerenter',()=>{activeIndex=index; menu.querySelectorAll('.fire-select-option').forEach((item,i)=>item.classList.toggle('is-active',i===index));}); row.addEventListener('click',()=>choose(index)); menu.append(row);});
    const availableBelow=innerHeight-rect.bottom-12, availableAbove=rect.top-12, goUp=availableBelow<220&&availableAbove>availableBelow; menu.dataset.direction=goUp?'up':'down'; menu.style.width=`${rect.width}px`; menu.style.left=`${Math.max(8,Math.min(rect.left,innerWidth-rect.width-8))}px`; menu.style.maxHeight=`${Math.max(120,Math.min(300,goUp?availableAbove:availableBelow))}px`; menu.style.top=goUp?`${Math.max(8,rect.top-menu.offsetHeight-7)}px`:`${Math.min(innerHeight-12,rect.bottom+7)}px`;
    document.body.append(menu); // recalculate after insertion for upward sheets
    if(goUp) menu.style.top=`${Math.max(8,rect.top-Math.min(menu.scrollHeight,Math.max(120,Math.min(300,availableAbove)))-7)}px`;
    select.setAttribute('aria-expanded','true'); menu.querySelector(`[aria-selected="true"]`)?.classList.add('is-active');
  };
  const onPointer = event => { const select=event.target.closest?.('select'); if(select) { event.preventDefault(); open(select); return; } if(!event.target.closest?.('.fire-select-menu')) close(); };
  const onKey = event => { const select=event.target.closest?.('select'); if(!select) return; if(event.key==='Escape'){close();return;} if(['Enter',' ','ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault(); if(!menu||active!==select){open(select);return;} const direction=event.key==='ArrowUp'?-1:1; const next=Math.max(0,Math.min(select.options.length-1,activeIndex+direction)); activeIndex=next; menu.querySelectorAll('.fire-select-option').forEach((item,i)=>item.classList.toggle('is-active',i===next)); if(event.key==='Enter'||event.key===' ')choose(next); } else if(event.key==='Home'||event.key==='End'){event.preventDefault(); if(!menu)open(select); activeIndex=event.key==='Home'?0:select.options.length-1; menu.querySelectorAll('.fire-select-option').forEach((item,i)=>item.classList.toggle('is-active',i===activeIndex));} };
  document.addEventListener('pointerdown',onPointer,true); document.addEventListener('keydown',onKey,true); window.addEventListener('resize',close); window.addEventListener('scroll',close,true);
  window.__dyfireSelectOverlay={destroy:()=>{close();document.removeEventListener('pointerdown',onPointer,true);document.removeEventListener('keydown',onKey,true);window.removeEventListener('resize',close);window.removeEventListener('scroll',close,true);delete window.__dyfireSelectOverlay;}};
  return window.__dyfireSelectOverlay;
}
function Card({children, className='', interactive=false}) {
  const ref = useRef(null);
  const trackPointer = event => {
    if (event.pointerType === 'touch' || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    ref.current.style.setProperty('--spot-x', `${event.clientX - rect.left}px`);
    ref.current.style.setProperty('--spot-y', `${event.clientY - rect.top}px`);
  };
  return <section ref={ref} className={`card ${interactive?'card-interactive':''} ${className}`} onPointerMove={trackPointer}>{children}</section>;
}
function Empty({title, text, action, icon='✦'}) { return <div className="empty"><div className="empty-mark">{icon}</div><h3>{title}</h3>{text && <p>{text}</p>}{action}</div>; }
function ContactQr({large=false}) {
  return <div className={`contact-qr-frame ${large?'contact-qr-large':''}`} role="img" aria-label="微信客服二维码">
    <img className="contact-qr-image contact-qr-light" src="/assets/wechat-support-qr-light.png" alt="微信客服二维码" />
    <img className="contact-qr-image contact-qr-dark" src="/assets/wechat-support-qr-dark.png" alt="微信客服二维码" aria-hidden="true" />
    <small>微信扫一扫</small>
  </div>;
}
function ContactSupportCard({compact=false, onOpen}) {
  return <Card className={`contact-support-card ${compact?'is-compact':''}`}>
    <div className="contact-support-copy">
      <span className="eyebrow">WECHAT SUPPORT</span>
      <h2>联系客服开通订阅</h2>
      <p>扫码添加微信，备注“火花 + 你的用户名”，客服会帮你选择套餐并开通。</p>
      <div className="contact-support-plans"><span>9.9 元 · 5 个好友任务</span><span>15 元 · 10 个好友任务</span><span>20 元 · 不限好友任务</span></div>
      {onOpen&&<Button variant="quiet" onClick={onOpen}>查看二维码</Button>}
    </div>
    <ContactQr />
  </Card>;
}
function ContactSupportModal({open, onClose}) {
  return <Drawer open={open} onClose={onClose} title="联系客服开通订阅" eyebrow="WECHAT SUPPORT" centered><div className="contact-support-modal"><ContactQr large/><h3>添加微信后备注你的用户名</h3><p>客服会根据你的需求推荐套餐并协助开通。套餐为 9.9 元 / 5 个好友任务、15 元 / 10 个好友任务、20 元 / 不限好友任务。</p><Button variant="quiet" onClick={onClose}>返回</Button></div></Drawer>;
}
function OnboardingGuide({data, membership, accounts, tasks, onNavigate}) {
  const [dismissed,setDismissed]=useState(Boolean(data?.onboarding?.dismissed));
  const [saving,setSaving]=useState(false);
  const [notice,setNotice]=useState('');
  useEffect(()=>setDismissed(Boolean(data?.onboarding?.dismissed)),[data?.onboarding?.dismissed]);
  const steps=[
    {key:'subscription',title:'开通订阅',text:'选择套餐并兑换管理员提供的兑换码。',done:Boolean(membership?.active),href:'/subscription',action:membership?.active?'查看订阅':'查看套餐'},
    {key:'account',title:'绑定抖音账号',text:'扫码登录并完成抖音安全验证。',done:accounts.length>0,href:'/accounts',action:accounts.length?'查看账号':'绑定账号'},
    {key:'friends',title:'创建续火任务',text:'选择好友和时间，建立每日计划。',done:tasks.length>0,href:'/studio',action:tasks.length?'管理任务':'创建任务'},
    {key:'runs',title:'核对第一次执行',text:'查看发送结果，确认任务按预期运行。',done:tasks.length>0&&Boolean(membership?.active),href:'/runs',action:'查看记录'},
  ];
  if(dismissed||steps.every(step=>step.done)) return null;
  const completed=steps.filter(step=>step.done).length;
  const close=async()=>{
    if(saving)return;
    setSaving(true);setNotice('');
    try{
      await api('/api/app/onboarding',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data?.csrf||'',dismissed:true})});
      setDismissed(true);
    }catch(error){setNotice(error.message||'暂时无法保存引导状态，请稍后重试。')}
    finally{setSaving(false)}
  };
  return <section className="onboarding-guide"><div className="onboarding-head"><div><span className="eyebrow">QUICK START</span><h2>第一次使用</h2><p>完成下面几步，就可以开始每天自动续火花。</p></div><button type="button" className="onboarding-dismiss" disabled={saving} onClick={close}>{saving?'正在保存…':'稍后再看'}</button></div><div className="onboarding-progress"><span><i style={{width:`${Math.round(completed/steps.length*100)}%`}}/></span><small>{completed}/{steps.length} 已完成</small></div><div className="onboarding-steps">{steps.map((step,index)=><div className={`onboarding-step ${step.done?'is-done':''}`} key={step.key}><span className="onboarding-number">{step.done?'✓':String(index+1).padStart(2,'0')}</span><div><b>{step.title}</b><p>{step.text}</p></div><Button variant="quiet" onClick={()=>onNavigate(step.href)}>{step.action}</Button></div>)}</div>{notice&&<p className="onboarding-notice" role="alert">{notice}</p>}</section>;
}
function Drawer({open, title, eyebrow, onClose, children, wide=false, centered=false}) {
  const closeRef=useRef(null);
  const dialogRef=useRef(null);
  const restoreRef=useRef(null);
  const onCloseRef=useRef(onClose);
  const closeTimerRef=useRef(null);
  const [mounted,setMounted]=useState(open);
  const [closing,setClosing]=useState(false);
  useEffect(()=>{onCloseRef.current=onClose},[onClose]);
  useEffect(()=>{
    if(open){
      if(closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
      setMounted(true); setClosing(false); return;
    }
    if(!mounted)return;
    setClosing(true);
    closeTimerRef.current=window.setTimeout(()=>{setMounted(false);setClosing(false)},220);
    return ()=>{if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current)};
  },[open,mounted]);
  useEffect(()=>{
    if(!open) return;
    document.body.classList.add('drawer-open');
    restoreRef.current=document.activeElement;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    const onKey=e=>{
      if(e.key==='Escape'){onCloseRef.current?.();return;}
      if(e.key!=='Tab')return;
      const dialog=dialogRef.current;
      if(!dialog)return;
      const focusable=[...dialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
        .filter(element=>{const style=window.getComputedStyle(element);return element.getAttribute('aria-hidden')!=='true'&&style.display!=='none'&&style.visibility!=='hidden';});
      const next=focusTrapTarget({key:e.key,shiftKey:e.shiftKey,activeElement:document.activeElement,focusable,contains:element=>dialog.contains(element)});
      if(next){e.preventDefault();next.focus();}
    };
    window.addEventListener('keydown',onKey);
    const focusTimer=window.setTimeout(()=>closeRef.current?.focus(),0);
    return ()=>{window.clearTimeout(focusTimer);document.body.style.overflow=previous;document.body.classList.remove('drawer-open');window.removeEventListener('keydown',onKey);restoreRef.current?.focus?.()};
  },[open]);
  const requestClose=()=>{setClosing(true);if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);closeTimerRef.current=window.setTimeout(()=>{setMounted(false);setClosing(false);onCloseRef.current?.()},220)};
  if (!mounted) return null;
  const drawer = <div className={`drawer-backdrop ${centered?'drawer-centered-backdrop':''} ${closing?'is-closing':''}`} onMouseDown={e=>e.target===e.currentTarget&&requestClose()}><aside ref={dialogRef} className={`drawer ${wide?'drawer-wide':''} ${centered?'drawer-centered':''} ${closing?'is-closing':''}`} role="dialog" aria-modal="true" aria-labelledby="drawer-title"><header className="drawer-head"><div><span className="eyebrow">{eyebrow}</span><h2 id="drawer-title">{title}</h2></div><button ref={closeRef} type="button" className="icon-button" onClick={requestClose} aria-label="关闭">×</button></header>{children}</aside></div>;
  return createPortal(drawer, document.body);
}
// Keep confirmations inside the React UI instead of blocking the browser with
// window.prompt(). The host is mounted once inside AppShell and exposes a
// small Promise-based bridge for non-component code.
function requestModal(options={}) {
  if (typeof window !== 'undefined' && typeof window.__dyfireModalRequest === 'function') {
    return window.__dyfireModalRequest(options);
  }
  // AppShell mounts the host immediately after the route content. A request
  // can arrive from an ancestor effect in the same commit, so wait briefly
  // for the bridge instead of silently skipping a required confirmation.
  return new Promise(resolve=>{
    const fallback=options.kind === 'prompt' ? null : false;
    const started=Date.now();
    const wait=()=>{
      if(typeof window !== 'undefined'&&typeof window.__dyfireModalRequest==='function'){
        window.__dyfireModalRequest(options).then(resolve,()=>resolve(fallback));
        return;
      }
      if(Date.now()-started>=1500){resolve(fallback);return;}
      window.setTimeout(wait,20);
    };
    wait();
  });
}
const confirmModal = options => requestModal({kind:'confirm', ...options});
function ModalHost() {
  const [request,setRequest]=useState(null);
  const activeRef=useRef(null);
  const queueRef=useRef([]);
  const showNext=useRef(()=>{});
  showNext.current=()=>{
    const next=queueRef.current.shift();
    if(!next){activeRef.current=null;setRequest(null);return;}
    activeRef.current=next;
    setRequest(next.options);
  };
  const ask=useRef(options=>new Promise(resolve=>{
    queueRef.current.push({options,resolve});
    if(!activeRef.current)showNext.current();
  }));
  useEffect(()=>{
    const bridge=options=>ask.current(options);
    window.__dyfireModalRequest=bridge;
    return()=>{if(window.__dyfireModalRequest===bridge)delete window.__dyfireModalRequest; const active=activeRef.current; if(active)active.resolve(false); queueRef.current.splice(0).forEach(item=>item.resolve(false));};
  },[]);
  const finish=value=>{
    const active=activeRef.current;
    if(!active)return;
    activeRef.current=null;
    setRequest(null);
    active.resolve(value);
    window.setTimeout(()=>showNext.current(),230);
  };
  const prompt=Boolean(request?.kind==='prompt');
  const [value,setValue]=useState('');
  useEffect(()=>{setValue(request?.defaultValue||'')},[request]);
  if(!request)return null;
  const submit=event=>{event.preventDefault();if(prompt&&!value.trim())return;finish(prompt?value:true)};
  return <Drawer open title={request.title||'请确认'} eyebrow={request.eyebrow||'CONFIRM ACTION'} centered onClose={()=>finish(prompt?null:false)}>
    <form className="drawer-content modal-host-content" onSubmit={submit}>
      {request.message&&<p className="modal-host-message">{request.message}</p>}
      {prompt&&<label className="modal-host-field"><span>{request.label||'输入内容'}</span><input className="input" autoFocus type={request.inputType||'password'} autoComplete={request.autoComplete||'current-password'} value={value} onChange={event=>setValue(event.target.value)} placeholder={request.placeholder||''} required/></label>}
      <div className="setting-actions modal-host-actions"><Button variant="quiet" type="button" onClick={()=>finish(prompt?null:false)}>{request.cancelLabel||'取消'}</Button><Button type="submit" disabled={prompt&&!value.trim()}>{request.confirmLabel||'确认'}</Button></div>
    </form>
  </Drawer>;
}
function Toast({message, onClose}) { useEffect(()=>{if(!message)return;const timer=window.setTimeout(onClose,4200);return()=>window.clearTimeout(timer)},[message,onClose]); if (!message) return null; return <div className="toast" role="status" aria-live="polite"><span>{message}</span><button type="button" onClick={onClose} aria-label="关闭提示">×</button></div>; }
function LoadingState({label='正在准备你的工作区…'}) { return <div className="loading-state" role="status" aria-live="polite"><span className="loading-spinner" aria-hidden="true"/><span>{label}</span></div>; }
function ErrorState({message,onRetry}) { return <div className="error-state" role="alert"><span className="error-state-mark" aria-hidden="true">!</span><div><h2>暂时无法读取</h2><p>{message}</p>{onRetry&&<Button variant="quiet" onClick={onRetry}>重新加载</Button>}</div></div>; }
class RouteErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = {error: null}; }
  static getDerivedStateFromError(error) { return {error}; }
  componentDidCatch(error, info) { console.error('dyfire route render failed', error, info); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="loading-screen"><div className="loading-mark">!</div><ErrorState message={this.state.error?.message || '页面渲染失败'} onRetry={()=>window.location.reload()}/><a href="/dashboard">返回首页</a></div>;
  }
}

function ServiceIndicator({operations}) {
  const initial = operations?.services ? serviceState(operations.services) : 'checking';
  const [state,setState] = useState(initial);
  useEffect(()=>{
    if (operations?.services) {
      setState(serviceState(operations.services));
      return undefined;
    }
    let cancelled=false;
    const check=()=>api('/health/ready',{timeoutMs:5000}).then(value=>{
      if(!cancelled)setState(value?.status==='ready'?'healthy':'degraded');
    }).catch(()=>{if(!cancelled)setState('degraded')});
    check();
    const timer=window.setInterval(check,30000);
    return()=>{cancelled=true;window.clearInterval(timer)};
  },[operations?.services]);
  const labels={checking:'检查服务',healthy:'服务正常',degraded:'部分不可用'};
  return <span className={`fire-service fire-service-${state}`} title={state==='degraded'?'服务状态异常，部分功能可能暂时不可用':'当前页面可用性状态'}><i aria-hidden="true"/>{labels[state]}</span>;
}
function serviceState(services={}) {
  const values=Object.values(services).map(item=>item?.status);
  const healthy=new Set(['healthy','运行中','online','在线']);
  return values.length&&values.every(status=>healthy.has(status))?'healthy':values.some(status=>healthy.has(status))?'degraded':'degraded';
}

function AppShell({data, children, refresh}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [theme,setTheme] = useState(()=>localStorage.getItem('dyfire-theme') || 'dark');
  const [toast,setToast] = useState('');
  const user = data?.user || {};
  const userName = displayName(user.name, '用户');
  const admin = user.role === 'admin';
  const isActive = href => location.pathname === href || (href === '/studio' && location.pathname.startsWith('/tasks')) || (href === '/admin/users' && (['/admin/subscriptions','/admin/tasks'].includes(location.pathname)||location.pathname.startsWith('/admin/tasks/groups/')||location.pathname.startsWith('/admin/customers/'))) || (href === '/admin/runs' && location.pathname==='/admin/today') || (href === '/admin/operations' && ['/admin/diagnostics','/admin/audit'].includes(location.pathname));
  const dockRoutes=getMobileDockRoutes(routes,adminRoutes,admin);
  // Keep the purchase path next to the home entry so new users can reach it
  // before setting up accounts or tasks.
  const topRoutes=admin?[...routes.slice(0,3),...adminRoutes]:[routes[0],routes[4],...routes.slice(1,4)];
  const activeTopRoute=topRoutes.find(([href])=>isActive(href))?.[0] || topRoutes[0]?.[0];
  const topNavItems=topRoutes.map(([href,label,IconComp])=>({value:href,label,icon:<span className="fire-nav-segment-icon" aria-hidden="true"><IconComp/></span>}));
  useEffect(()=>{document.documentElement.dataset.theme=theme; localStorage.setItem('dyfire-theme',theme)},[theme]);
  useEffect(()=>{resetRouteScroll(location)},[location.pathname]);
  useEffect(()=>{
    document.querySelectorAll('input.search-input,input.compact-search,.admin-run-toolbar input').forEach(input=>{
      if(!input.getAttribute('aria-label')) input.setAttribute('aria-label',input.getAttribute('placeholder')||'搜索');
    });
  },[location.pathname]);
  useEffect(()=>{if (location.state?.toast) { setToast(location.state.toast); navigate(location.pathname,{replace:true,state:{}}); }},[location,navigate]);
  const logout = () => { const form=document.createElement('form'); form.method='POST'; form.action='/logout'; const input=document.createElement('input'); input.name='csrf_token'; input.value=data?.csrf||''; form.append(input); document.body.append(form); form.submit(); };
  const navLink=([href,label,IconComp])=><Link key={href} className={isActive(href)?'active':''} aria-current={isActive(href)?'page':undefined} to={href}><IconComp/>{label}</Link>;
  const dockLink=([href,label,IconComp])=><Link key={href} className={isActive(href)?'active':''} aria-current={isActive(href)?'page':undefined} to={href}><IconComp/><small>{label}</small></Link>;
  return <div className="app-shell fire-shell">
    <main className="main-shell fire-main" id="workspace"><header className="fire-nav"><div className="fire-nav-brand"><Logo/></div><RubberSegment className="fire-nav-segment" items={topNavItems} value={activeTopRoute} onChange={href=>navigate(href)} trackColor={theme==='light'?'rgba(255,255,255,.62)':'rgba(255,255,255,.035)'} thumbColor={theme==='light'?'#e55b20':'#ff6b25'} textColor={theme==='light'?'#74645b':'#a88f83'} activeTextColor={theme==='light'?'#fff8f2':'#20100a'} size="md" radius={999} inset={5} equalSlots stretch={100} squash={3} speed={1} glide={75} draggable aria-label={admin?'管理导航':'工作区导航'}/><div className="fire-nav-right"><ServiceIndicator operations={data?.operations}/><button type="button" className="fire-theme" onClick={()=>setTheme(theme==='dark'?'light':'dark')} aria-label="切换主题"><ThemeIcon light={theme==='light'}/></button><button type="button" className="fire-account" onClick={()=>navigate(admin?'/admin/settings':'/account-center')}><span className="avatar avatar-small">{initial(userName).toUpperCase()}</span><span>{admin?'管理员':userName}</span></button></div></header><div className="mobile-route-status">当前：{topRoutes.find(([href])=>href===activeTopRoute)?.[1]||'首页'}</div><div className="main-inner"><RouteMotionDiv locationKey={location.pathname}>{children}</RouteMotionDiv></div></main>
    <nav className="mobile-dock" aria-label="移动端主导航">{dockRoutes.map(dockLink)}</nav><Toast message={toast} onClose={()=>setToast('')}/><ModalHost/>
  </div>;
}

function PageHeader({eyebrow, title, text, actions}) { return <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{text&&<p>{text}</p>}</div>{actions&&<div className="page-actions">{actions}</div>}</header>; }
function Metric({label,value,detail,tone=''}) { return <Card className="metric"><span>{label}</span><strong className={tone}>{value}</strong>{detail&&<small>{detail}</small>}</Card>; }
function AccountStatus({account={}, onAction}) { const health=account.health||{}; const name=displayName(account.name); const good=health.state==='valid'||account.validation_state==='valid'; const tone=good?'good':(health.state==='verification'||health.blocked?'danger':'warn'); return <div className="account-status-row"><span className="avatar">{account.profile?.avatar_url?<img src={account.profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><b>{name}</b><small>{account.profile?.nickname && account.profile.nickname!==name?account.profile.nickname+' · ':''}{health.label||'等待复核'}</small></div><Badge tone={tone}>{health.label||'等待复核'}</Badge><Button variant="quiet" onClick={()=>onAction?.(account)}>{good?'查看':'处理'}</Button></div>; }

function Dashboard({data,onRefresh}) {
  const navigate=useNavigate(); const tasks=asList(data?.tasks); const accounts=asList(data?.accounts); const runs=asList(data?.runs); const membership=data.membership||{};
  const [supportOpen,setSupportOpen]=useState(false);
  const success=runs.filter(r=>r.status==='success').length; const failed=runs.filter(r=>['failed','submitted_unconfirmed'].includes(r.status)).length; const valid=accounts.filter(a=>a.health?.state==='valid'||a.validation_state==='valid').length; const pct=accounts.length?Math.round(valid/accounts.length*100):0;
  const runByTaskId=new Map(); runs.forEach(run=>{if(run?.taskId&&!runByTaskId.has(run.taskId))runByTaskId.set(run.taskId,run)});
  const friends=[...new Map(tasks.filter(t=>t?.enabled||runByTaskId.has(t?.id)).map(t=>[t?.sec_uid||`${t?.accountId}:${t?.name}`,t])).values()];
  const activity=data.activity||{}; const heatDays=asList(activity.days); while(heatDays.length&&heatDays.length%7)heatDays.push({date:'',count:0,outside:true});
  const heatColumns=Math.max(1,Math.ceil(heatDays.length/7)); const heatMonths=Array.from({length:heatColumns},(_,column)=>{const date=heatDays[column*7]?.date||'';if(!date)return '';const month=Number(date.slice(5,7));const day=Number(date.slice(8,10));return day<=7?`${month}月`:''});
  const animPct=useCountUp(pct,{duration:1100,enabled:accounts.length>0});
  const animValid=useCountUp(valid,{duration:900,enabled:accounts.length>0});
  const animTotal=useCountUp(accounts.length,{duration:900,enabled:accounts.length>0});
  const [editTask,setEditTask]=useState(null);
  const doRefresh=()=>onRefresh?.();
  return <div className="fire-home">
    <div className="fire-home-toolbar"><div><SplitText className="fire-kicker" stagger={14}>DYFIRE / DAILY RITUALS</SplitText><span className="fire-date">北京时间 · {new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric'})}</span></div><div className="fire-home-actions"><ServiceIndicator operations={data?.operations}/><Button variant="quiet" onClick={()=>navigate('/runs')}>执行记录 ↗</Button><MagneticButton onClick={()=>navigate(accounts.length?'/studio':'/accounts')}>{accounts.length?'新建任务':'绑定账号'} ↗</MagneticButton></div></div>
    <section className="fire-home-hero"><div className="fire-hero-copy"><span className="fire-kicker">账号健康 · 今日状态</span><strong>{accounts.length?`${animValid}/${animTotal}`:'—'}</strong><h1>账号登录有效</h1><p>{accounts.length?(pct===100?'所有账号都可以正常续火。':`${accounts.length-valid} 个账号需要重新确认登录。`):'绑定抖音账号后，系统会在这里显示健康状态。'}</p><div className="fire-hero-chips"><span className="fire-chip good">● {success} 已发出</span><span className="fire-chip wait">● {runs.length-success-failed} 执行中</span><span className="fire-chip bad">● {failed} 待处理</span></div></div><div className="fire-ring"><svg viewBox="0 0 110 110"><circle cx="55" cy="55" r="47"/><circle className="ring-progress" cx="55" cy="55" r="47" style={{strokeDashoffset:`${295.3-(295.3*animPct/100)}`}}/></svg><b>{animPct}%</b><small>账号有效</small></div><span className="fire-hero-mark">✦</span></section>
    <OnboardingGuide data={data} membership={membership} accounts={accounts} tasks={tasks} onNavigate={navigate}/>
    <div className="fire-home-main"><section><div className="fire-section-title">续火花任务 <span/></div><div className="fire-friend-grid">{friends.length?friends.slice(0,8).map((t,i)=>{const last=runByTaskId.get(t.id); return <FriendCard key={t.id||i} task={t} last={last} state={friendState(t,last)} onEdit={setEditTask}/>;}) : <Empty title="还没有今日续火任务" text="创建任务后会显示今日执行状态。"/>}{friends.length<4&&<button type="button" className="fire-friend-card fire-friend-add" onClick={()=>navigate(accounts.length?'/studio':'/accounts')}><span>＋</span><b>添加目标好友</b><small>从已同步会话中选择</small></button>}</div></section><aside className="fire-home-sidebar"><section className="fire-sub-card"><span className="fire-kicker">SUBSCRIPTION</span><h2>我的订阅</h2><strong>{membership.active?(membership.days_remaining??'∞'):'—'}<small>{membership.active?'天有效':'未开通'}</small></strong><p>{membership.active?`启用任务 ${membershipTaskUsage(membership)??membership.task_count??data?.resource_limits?.tasks_enabled_total??tasks.filter(task=>task.enabled).length} / ${membership.task_limit_label||membership.max_tasks||'不限'}`:'联系客服购买后即可开通。'}</p><MembershipPlanNotice membership={membership} compact/><Button variant="quiet" onClick={()=>navigate('/subscription')}>{membership.active?'管理订阅':'查看套餐'}</Button>{!membership.active&&<button type="button" className="fire-support-link" onClick={()=>setSupportOpen(true)}>联系客服开通</button>}</section></aside></div>
    <div className="fire-home-bottom"><section className="fire-heat-card"><div className="fire-section-title">近 365 天互动 · {activity.total||0} 次 <span/></div><p className="fire-heat-hint">每格代表一天，颜色越亮表示当天互动越多</p><div className="fire-heat-months" style={{gridTemplateColumns:`repeat(${heatColumns},minmax(0,1fr))`}}>{heatMonths.map((label,i)=><span key={i}>{label}</span>)}</div><div className="fire-heat-grid" style={{gridTemplateColumns:`repeat(${heatColumns},minmax(0,1fr))`}}>{heatDays.map((day,i)=><i key={i} className={day.outside?'outside':''} style={{opacity:day.outside?0:Math.min(1,.12+(Number(day.count||0)/Math.max(1,Math.max(...heatDays.map(item=>Number(item.count||0)))))*.88)}} title={day.date?`${day.date} · ${day.count||0} 次互动`:''}/>)}</div><div className="fire-heat-legend"><span>少</span><i/><i/><i/><i/><span>多</span></div></section><section className="fire-quick-card"><span>保持日常</span><h2>把重要的人，交给稳定的计划。</h2><MagneticButton onClick={()=>navigate(accounts.length?'/studio':'/accounts')}>{accounts.length?'创建续火任务':'绑定账号'} ↗</MagneticButton></section></div>
    <TaskEditDrawer task={editTask} data={data} open={Boolean(editTask)} onClose={()=>setEditTask(null)} onRefresh={doRefresh}/><ContactSupportModal open={supportOpen} onClose={()=>setSupportOpen(false)}/>
  </div>;
}

function friendState(task,last){
  if(last?.status==='success')return {label:'已完成',tone:'good'};
  if(last?.status==='failed')return {label:'失败',tone:'danger'};
  if(last?.status==='submitted_unconfirmed')return {label:'待核实',tone:'warn'};
  if(last?.status==='running'||last?.status==='retrying')return {label:'执行中',tone:'warn'};
  return task?.enabled?{label:'待发送',tone:'warn'}:{label:'已暂停',tone:'neutral'};
}

// FriendCard with pointer-tracking spotlight (same pattern as Card)
function FriendCard({task: t, last, state, onEdit}) {
  const ref = useRef(null);
  const track = e => {
    if (e.pointerType === 'touch' || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    ref.current.style.setProperty('--spot-x', `${e.clientX - r.left}px`);
    ref.current.style.setProperty('--spot-y', `${e.clientY - r.top}px`);
  };
  const inner = <>
    <div className="fire-friend-top">
      <span className="fire-friend-avatar">{t.avatar?<img src={t.avatar} alt=""/>:initial(t.name)}</span>
      <Badge tone={state?.tone||'neutral'}>{state?.label||'待发送'}</Badge>
    </div>
    <b>{displayName(t.name)}</b>
    {t.streak != null && <strong><span className="fire-streak-icon" aria-hidden="true">🔥</span> {t.streak}<small> 天连续</small></strong>}
    <time>{last?.time||`今日 ${t.time||'—'}`} · 今日</time>
    <span className="fire-friend-bar"><i style={{transform:`scaleX(${t.enabled?1:.2})`}}/></span>
  </>;
  if (onEdit) {
    return (
      <button ref={ref} type="button" className="fire-friend-card" onClick={()=>onEdit(t)} onPointerMove={track}>
        {inner}
      </button>
    );
  }
  return (
    <Link ref={ref} className="fire-friend-card" to={`/tasks/${t.id}/edit`} onPointerMove={track}>
      {inner}
    </Link>
  );
}

function ContactPicker({open,onClose,account,data,onDone,releasedKeys=new Set()}) {
  const controllerRef=useRef(null);
  if(!controllerRef.current)controllerRef.current=createContactPickerController({request:api});
  const controller=controllerRef.current;
  const [state,setState]=useState(()=>controller.getState());
  const [query,setQuery]=useState('');
  useEffect(()=>controller.subscribe(setState),[controller]);
  useEffect(()=>{
    if(!open||!account?.id){controller.close();return;}
    setQuery('');
    void controller.open(account.id,data?.csrf||'');
    return()=>controller.close();
  },[open,account?.id,controller,data?.csrf]);
  const {items,selected,loading,busy,error,page}=state;
  const existing=new Set(asList(data?.tasks).filter(t=>t?.accountId===account?.id).map(contactKey));
  releasedKeys.forEach(key=>existing.delete(key));
  const visible=items.filter(i=>[i?.name,i?.nickname,i?.remark_name,i?.unique_id,i?.short_id].filter(Boolean).join(' ').toLowerCase().includes(query.toLowerCase()));
  const actionLabel=busy==='page'?'正在加载更多…':busy==='sync'?'正在同步好友…':page.dbHasMore?'加载更多好友':page.activeSync?'继续同步好友':page.canSyncMore?'同步更多好友':'重新同步好友';
  const coverageLabel=page.dbHasMore?`已显示 ${items.length} / ${page.total} 位已同步好友`:`已显示 ${items.length} 位已同步好友`;
  const coverageHint=page.dbHasMore?'继续加载已同步的好友，搜索会覆盖加载后的列表。':page.syncHasMore===true&&page.syncLimit>=100?'当前同步最多读取最近 100 个会话，可重新同步更新。':page.syncHasMore===false?'本次同步未返回更多会话，可重新同步检查。':'列表来自已同步会话，可继续同步更多好友。';
  const close=()=>{controller.close();onClose()};
  return <Drawer open={open} onClose={close} title="选择目标好友" eyebrow="SELECT CONNECTIONS" wide>
    <div className="drawer-content contact-picker-content">
      <input className="input search-input" autoFocus aria-label="搜索已同步好友" value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入昵称或备注，搜索已加载会话"/>
      <div className="drawer-tools"><span>{selected.size} 人已选</span><button type="button" onClick={()=>controller.selectMany(visible.filter(i=>!existing.has(contactKey(i))).map(contactKey))} disabled={loading}>全选当前结果</button><button type="button" onClick={()=>controller.clearSelection()} disabled={loading}>清空</button></div>
      {loading&&!items.length?<div className="skeleton-stack"><div/><div/><div/></div>:<div className="contact-list">{visible.map((item,i)=>{
        const id=contactKey(item)||`contact-${i}`;const name=displayName(item?.name,'未命名好友');const disabled=existing.has(id);
        return <label className={`contact-row ${disabled?'disabled':''}`} key={id}><input type="checkbox" disabled={disabled} checked={selected.has(id)} onChange={e=>controller.select(id,e.target.checked)}/><span className="avatar">{item?.avatar_url?<img src={item.avatar_url} alt=""/>:initial(name)}</span><span className="grow"><b>{name}</b><small>{disabled?'已有续火任务':item?.remark_name||item?.nickname||'已同步会话'}</small></span><span>{selected.has(id)?'✓':''}</span></label>
      })}</div>}
      {!loading&&!error&&!visible.length&&<Empty title={query?'没有匹配的已加载会话':'还没有已同步好友'} text={query?'搜索仅覆盖已加载会话，可以加载或同步更多好友后再搜索。':'点击下方同步按钮，读取这个账号的最近好友会话。'}/>}
    </div>
    <footer className="drawer-foot contact-picker-footer">
      <div className="contact-picker-coverage" aria-live="polite"><b>{coverageLabel}</b><small>{busy==='sync'?'同步可能需要几分钟，关闭后可重新打开查看进度。':coverageHint}</small></div>
      {error&&<div className="contact-picker-error" role="alert"><span>{error}</span>{!items.length&&<button type="button" disabled={loading||Boolean(busy)} onClick={()=>controller.retry()}>重新加载</button>}</div>}
      <div className="contact-picker-actions"><span>{selected.size} 位好友</span><Button type="button" variant="quiet" disabled={loading||Boolean(busy)} onClick={()=>controller.next()}>{actionLabel}</Button><Button type="button" disabled={!selected.size} onClick={()=>{onDone([...selected].map(id=>items.find(i=>contactKey(i)===id)).filter(Boolean));close()}}>完成选择 ↗</Button></div>
    </footer>
  </Drawer>;
}

function scheduleFirstRunLabel(entry) {
  if (!entry?.next_run_at) return '首次执行时间待确认';
  const date=new Date(entry.next_run_at);
  if (Number.isNaN(date.getTime())) return '首次执行时间待确认';
  const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  const tomorrow=new Date(Date.now()+86400000).toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  const runDay=date.toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  const day=runDay===today?'今天':runDay===tomorrow?'明天':date.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric'});
  return `${day} ${entry.time||date.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false})}`;
}

function Studio({data,onRefresh,preEditId,onEditClose}) {
  const accounts=asList(data?.accounts); const membership=data?.membership||{};
  const [composerStep,setComposerStep]=useState(1);
  const [accountId,setAccountId]=useState(accounts[0]?.id||'');
  const [selectedTime,setSelectedTime]=useState('');
  const [kind,setKind]=useState('douyin_sticker'); const [message,setMessage]=useState('');
  const [friends,setFriends]=useState([]); const [picker,setPicker]=useState(false);
  const [search,setSearch]=useState(''); const [filter,setFilter]=useState('all'); const [notice,setNotice]=useState(''); const [successToast,setSuccessToast]=useState('');
  const [taskRows,setTaskRows]=useState(()=>asList(data?.tasks));
  const [taskPage,setTaskPage]=useState(()=>data?.tasks_pagination?.page||1);
  const [taskPagination,setTaskPagination]=useState(()=>data?.tasks_pagination||null);
  const [loadingMoreTasks,setLoadingMoreTasks]=useState(false); const [createdPending,setCreatedPending]=useState(0);
  const [releasedKeys,setReleasedKeys]=useState(()=>new Set());
  const refreshRef=useRef(onRefresh); refreshRef.current=onRefresh;
  const successRef=useRef(null);
  const composer=useMemo(()=>createTaskComposerController({request:api, refresh:()=>refreshRef.current?.(), onCreated:value=>{
    const added=asList(value?.items);
    setReleasedKeys(keys=>{
      if(!keys.size)return keys;
      const next=new Set(keys);
      added.forEach(item=>next.delete(contactKey(item)));
      return next;
    });
    setTaskRows(rows=>mergeTaskRows(rows,added));
    setTaskPagination(page=>page?{...page,total:page.total+added.length}:page);
    setCreatedPending(count=>count+added.length);
    setSuccessToast(`已成功创建 ${value.count??added.length} 个续火任务`);
    setFilter('all');setSearch('');setFriends([]);setSelectedTime('');setComposerStep(1);setNotice('');
  }}),[]);
  const [schedule,setSchedule]=useState(()=>composer.getState());
  useEffect(()=>{const unsubscribe=composer.subscribe(setSchedule);return()=>{unsubscribe();composer.close()}},[composer]);
  const selectedTargets=useMemo(()=>friends.map(friend=>({name:friend.name,sec_uid:friend.sec_uid||''})),[friends]);
  useEffect(()=>{composer.configure({accountId,count:Math.max(1,friends.length),targets:selectedTargets,csrf:data.csrf,mode:selectedTime?'manual':'auto',preferred:selectedTime})},[composer,accountId,selectedTargets,selectedTime,data.csrf]);
  useEffect(()=>{if(!accountId&&accounts[0]?.id)setAccountId(accounts[0].id)},[accountId,accounts[0]?.id]);
  useEffect(()=>{
    if(schedule.success){successRef.current?.focus({preventScroll:true});successRef.current?.scrollIntoView({behavior:'smooth',block:'nearest'})}
  },[schedule.success]);
  const saving=schedule.creating;
  const tasks=taskRows;
  useEffect(()=>{
    const trigger=document.querySelector('.friend-trigger');
    if(!trigger)return;
    trigger.setAttribute('role','button');trigger.setAttribute('tabindex',accountId?'0':'-1');
    trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded',String(picker));trigger.setAttribute('aria-disabled',String(!accountId));
    const onKey=event=>{if((event.key==='Enter'||event.key===' ')&&accountId){event.preventDefault();setPicker(true)}};
    trigger.addEventListener('keydown',onKey);return()=>trigger.removeEventListener('keydown',onKey);
  },[accountId,picker,composerStep]);
  useEffect(()=>{
    setTaskRows(asList(data?.tasks));setTaskPage(data?.tasks_pagination?.page||1);setTaskPagination(data?.tasks_pagination||null);setCreatedPending(0);
  },[data?.tasks,data?.tasks_pagination]);
  const loadMoreTasks=async()=>{
    if(loadingMoreTasks||!taskPagination?.has_next)return;
    setLoadingMoreTasks(true);
    try{const next=await api(`/api/app/tasks?page=${taskPage+1}`);setTaskRows(rows=>mergeTaskRows(rows,asList(next?.items)));setTaskPage(next?.pagination?.page||taskPage+1);setTaskPagination(next?.pagination||taskPagination)}
    catch(error){setNotice(error.message)}finally{setLoadingMoreTasks(false)}
  };
  const [editTask,setEditTask]=useState(()=>preEditId?tasks.find(t=>t?.id===preEditId)||null:null);
  const account=accounts.find(a=>a?.id===accountId);
  const shown=tasks.filter(t=>(filter==='all'||(filter==='active'?t?.enabled:!t?.enabled))&&(!search||displayName(t?.name,'').toLowerCase().includes(search.toLowerCase())));
  const closeEdit=()=>{setEditTask(null);onEditClose?.()};
  const deleteTask=async task=>{
    if(!task?.id)return;
    const name=displayName(task.name,'未命名好友');
    if(!await confirmModal({title:'删除续火任务',eyebrow:'TASK WORKBENCH',message:`确定删除发给 ${name} 的任务吗？删除后可重新选择该好友。`,confirmLabel:'确认删除'}))return;
    try {
      await api(`/api/studio/tasks/${task.id}`,{method:'DELETE',headers:{'Content-Type':'application/json','X-CSRF-Token':data?.csrf||''}});
      setTaskRows(rows=>rows.filter(item=>item?.id!==task.id));
      setTaskPagination(page=>page?{...page,total:Math.max(0,(page.total??1)-1)}:page);
      setReleasedKeys(keys=>{
        const next=new Set(keys);
        [task?.sec_uid,task?.name].filter(Boolean).forEach(key=>next.add(key));
        return next;
      });
      setEditTask(current=>current?.id===task.id?null:current);
      setNotice(`已删除 ${name} 的续火任务，现在可以重新选择该好友。`);
      await onRefresh?.();
    } catch(error) { setNotice(error.message); }
  };
  const createTask=async()=>{
    if(!friends.length)return setNotice('请选择一个或多个好友');
    if(!account)return setNotice('请选择发送账号');
    if(kind==='text'&&!message.trim())return setNotice('请填写发送内容');
    setNotice('');
    await composer.create({csrf:data.csrf,payload:{account_id:account.id,targets:friends.map(f=>({name:f.name,sec_uid:f.sec_uid||''})),message_kind:kind,message_template:kind==='text'?message.trim():'续火花'}});
  };
  const submit=async event=>{
    event.preventDefault();setNotice('');
    if(composerStep===1){if(!account)return setNotice('请先选择发送账号');setComposerStep(2);return}
    if(composerStep===2){if(!friends.length)return setNotice('请选择一个或多个好友');setComposerStep(3);return}
    await createTask();
  };
  const enabledTotal=data?.resource_limits?.tasks_enabled_total!=null?data.resource_limits.tasks_enabled_total+createdPending:tasks.filter(t=>t?.enabled).length;
  const pausedTotal=data?.resource_limits?.tasks_paused_total??Math.max(0,(taskPagination?.total??tasks.length)-enabledTotal);
  const firstMinutes=time=>{const [hour,minute]=time.split(':').map(Number);return hour*60+minute};
  const ready=schedule.status==='ready';
  const scheduledFriends=ready?schedule.plan.targets||friends:friends;
  const closeSuccessToast=React.useCallback(()=>setSuccessToast(''),[]);
  return <>
    <PageHeader eyebrow="02 / ROUTINE STUDIO" title="续火任务" text="把每天的默契交给稳定、可核对的自动计划。" actions={<Button onClick={()=>document.getElementById('task-composer')?.scrollIntoView({behavior:'smooth'})}>新建任务 ↘</Button>}/>
    {schedule.success&&<section className="task-create-success" ref={successRef} tabIndex={-1} role="status" aria-live="polite">
      <div className="task-create-success-head"><span className="task-created-mark" aria-hidden="true">✓</span><div><h2>已创建 {schedule.success.count} 个续火任务</h2><p>每天按以下时间发送，已加入当前任务列表。所有时间均为北京时间。</p></div></div>
      <ul className="task-created-list">{schedule.success.items.map((task,index)=><li key={task.id}><b>{displayName(task.name,'未命名好友')}</b><span>每天 <time>{task.time}</time></span><small>首次：{scheduleFirstRunLabel({...schedule.success.entries[index],time:task.time})}</small></li>)}</ul>
      {schedule.success.refreshError&&<p className="task-success-refresh">任务已保存，页面数据暂未刷新。<Button variant="quiet" onClick={()=>refreshRef.current?.()}>刷新列表</Button></p>}
    </section>}
    <div className="studio-layout"><section className="studio-list">
      <div className="toolbar"><RubberSegment className="admin-segment" items={[{value:'all',label:`全部 ${taskPagination?.total??tasks.length}`},{value:'active',label:`运行中 ${enabledTotal}`},{value:'paused',label:`已暂停 ${pausedTotal}`}]} value={filter} onChange={setFilter} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="sm" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="任务筛选"/><input className="input compact-search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索好友或备注"/></div>
      {shown.length?<div className="task-table"><div className="table-head"><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div>{shown.map((task,index)=>{const name=displayName(task?.name,'未命名好友');const actionClass='icon-action';return <div className="task-row" key={task?.id||`${name}-${index}`}><span className="person-cell"><span className="avatar">{task?.avatar?<img src={task.avatar} alt=""/>:initial(name)}</span><span><b>{name}</b><small>{displayText(task?.kind,'续火花表情')}</small></span></span><time>{displayText(task?.time,'—')}<small>每天</small></time><Badge tone={task?.enabled?'good':'neutral'}>{task?.enabled?'已启用':'已暂停'}</Badge><span className="row-actions"><Button className={actionClass} variant="quiet" title="编辑任务" aria-label={`编辑 ${name}`} onClick={()=>setEditTask(task)}><ActionEditIcon/></Button><Button className={actionClass} variant="quiet" title={task?.enabled?'暂停任务':'恢复任务'} aria-label={`${task?.enabled?'暂停':'恢复'} ${name}`} onClick={async()=>{try{await api(`/api/studio/tasks/${task?.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':data?.csrf},body:JSON.stringify({enabled:!task?.enabled})});await onRefresh()}catch(error){setNotice(error.message)}}}>{task?.enabled?<ActionPauseIcon/>:<ActionPlayIcon/>}</Button><Button className={actionClass} variant="danger" title="删除任务" aria-label={`删除 ${name}`} onClick={()=>deleteTask(task)}><ActionDeleteIcon/></Button></span></div>})}</div>:<Empty title="还没有续火任务" text="先选择账号和好友，创建第一条每日计划。"/>}
      {taskPagination?.has_next&&<div className="setting-actions"><Button variant="quiet" disabled={loadingMoreTasks} onClick={loadMoreTasks}>{loadingMoreTasks?'正在加载…':`加载更多任务（已显示 ${tasks.length} / ${taskPagination.total}）`}</Button></div>}
    </section><Card className="composer-card" id="task-composer">
      <div className="section-head"><div><span className="eyebrow">NEW CONNECTION</span><h2>新建续火任务</h2></div><span className="step-mark">0{composerStep}</span></div>
      <div className="composer-steps" role="list" aria-label="创建任务步骤">{[['账号',1],['好友',2],['发送设置',3]].map(([label,step])=><button type="button" key={step} className={composerStep===step?'active':''} disabled={step>composerStep||saving} aria-current={composerStep===step?'step':undefined} onClick={()=>step<=composerStep&&setComposerStep(step)}><b>0{step}</b><span>{label}</span></button>)}</div>
      <form onSubmit={submit} className="form-stack">
        {composerStep===1&&<><label>发送账号<Select className="input" value={accountId} disabled={saving} onChange={event=>{setAccountId(event.target.value);setFriends([]);setSelectedTime('')}} aria-label="发送账号">{accounts.length?accounts.map((item,index)=><option key={item?.id||index} value={item?.id||''}>{displayName(item?.name)}</option>):<option value="">请先绑定账号</option>}</Select></label><div className="composer-step-hint">先选择负责发送消息的抖音账号。</div>{!accounts.length&&<div className="inline-feedback" role="status">尚未绑定发送账号。<Link to="/accounts">前往抖音账号页 ↗</Link></div>}</>}
        {composerStep===2&&<><label>目标好友<div className="friend-trigger" onClick={()=>account&&setPicker(true)}>{friends.length?friends.map((friend,index)=><span key={friend?.sec_uid||friend?.name||index} className="selected-chip">{displayName(friend?.name,'未命名好友')}</span>):<span className="muted">选择一个或多个好友</span>}<span>＋</span></div></label><div className="composer-step-hint">可选择多位好友，下一步会显示每位好友的具体发送时间。已有任务的好友会自动标记。</div></>}
        {composerStep===3&&<>
          <div className="composer-summary"><span>发送账号</span><b>{displayName(account?.name,'—')}</b><span>目标好友</span><b>{scheduledFriends.length?`${scheduledFriends.length} 位好友`:'—'}</b></div>
          <div className="form-grid"><label>每日发送时间<input className="input" type="time" aria-label="每日发送时间" value={selectedTime||schedule.plan?.times?.[0]||''} disabled={saving||schedule.status==='loading'} onChange={event=>setSelectedTime(event.target.value)}/></label><label>消息类型<Select className="input" value={kind} disabled={saving} onChange={event=>setKind(event.target.value)} aria-label="消息类型"><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></Select></label></div>
          <section className="schedule-preview" aria-label="好友发送时间安排" aria-busy={schedule.status==='loading'}>
            <div className="schedule-preview-head"><h3>每位好友的每日发送时间</h3><small>北京时间</small></div>
            {schedule.status==='loading'&&<p role="status">正在安排发送时间…</p>}
            {schedule.error&&<div className="inline-feedback danger-text" role="alert"><p>{schedule.error}</p><Button variant="quiet" type="button" disabled={saving} onClick={()=>composer.retry()}>重新获取时间安排</Button></div>}
            {schedule.conflict&&<div className="inline-feedback schedule-conflict" role="alert"><b>请核对新的时间安排</b><p>{schedule.conflict}</p></div>}
            {ready&&<><p className="schedule-reason">{scheduledFriends.length>1?'已为每位好友自动错开发送时间，请确认下方安排。':'创建后每天按此时间发送。'}</p>{scheduledFriends.length<friends.length&&<p className="schedule-reason">已合并重复选择的好友，按 {scheduledFriends.length} 位好友创建任务。</p>}<ul className="schedule-friend-list">{scheduledFriends.map((friend,index)=>{const entry=schedule.plan.entries[index];const offset=(firstMinutes(entry.time)-firstMinutes(schedule.plan.times[0])+1440)%1440;return <li key={friend.sec_uid||friend.name||index}><span className="schedule-friend-name">{displayName(friend.name,'未命名好友')}</span><time>{entry.time}</time><small className="schedule-offset">{index?`较首位晚 ${offset} 分钟`:'首位好友'}</small><small className="schedule-first-run">首次：{scheduleFirstRunLabel(entry)}</small></li>})}</ul></>}
          </section>
          {kind==='text'&&<label>发送内容<textarea className="input textarea" maxLength="300" disabled={saving} value={message} onChange={event=>setMessage(event.target.value)} placeholder="写一句日常问候"/></label>}
          <div className="message-preview"><span className="preview-mark">✦</span><div><b>{kind==='text'?'自定义文字消息':'续火花表情'}</b><small>{kind==='text'?'按以上时间发送你填写的内容。':'每天按以上时间，给选中的好友发送续火花表情。'}</small></div></div>
        </>}
        {notice&&<div className="inline-feedback" role="status">{notice}</div>}
        {schedule.createError&&<div className="inline-feedback danger-text" role="alert"><b>尚未创建任务，已保留当前填写内容</b><p>{schedule.createError}</p></div>}
        <div className="composer-actions">{composerStep>1&&<Button variant="quiet" type="button" disabled={saving} onClick={()=>setComposerStep(step=>step-1)}>上一步</Button>}<Button type="submit" disabled={saving||!membership.can_add_task||(composerStep===3&&!ready)}>{saving?'正在创建…':composerStep===3?schedule.conflict?'确认新安排并创建 ↗':`确认创建 ${scheduledFriends.length} 个任务 ↗`:'下一步 ↗'}</Button></div>
        {!membership.can_add_task&&<small className="muted">当前订阅不可创建新任务，请先查看订阅。</small>}
      </form>
    </Card></div>
    <ContactPicker open={picker} onClose={()=>setPicker(false)} account={account} data={data} releasedKeys={releasedKeys} onDone={value=>{if(value)setFriends(value)}}/><TaskEditDrawer task={editTask} data={data} open={Boolean(editTask)} onClose={closeEdit} onRefresh={onRefresh}/><Toast message={successToast} onClose={closeSuccessToast}/>
  </>;
}

function ScanVerification({scan,data,setScan}) {
  const [value,setValue]=useState(''); const [sending,setSending]=useState(false); const [notice,setNotice]=useState('');
  const needs=Boolean(scan?.needs_verification);
  if(!needs)return null;
  const password=scan?.verification_kind==='password';
  const submit=async e=>{e.preventDefault();if(!value.trim()||!scan?.id)return;setSending(true);setNotice('正在提交验证…');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','text');fd.append('request_id',interactionRequestId());fd.append('text',value);const accepted=await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd});setValue('');let state=accepted;for(let attempt=0;attempt<24&&['queued','processing'].includes(state?.status);attempt++){await new Promise(resolve=>setTimeout(resolve,650));state=await api(state.status_url)}setNotice(state?.status==='delivered'?'云端已确认验证输入':state?.status==='failed'?'云端未接受验证输入，请查看画面后重试':state?.status==='uncertain'?'验证结果暂时无法确认，请查看云端页面':'已提交，等待抖音确认');const latest=await api(`/accounts/scan/${scan.id}`);if(latest&&typeof latest==='object')setScan(latest)}catch(err){setNotice(err.message)}finally{setSending(false)}};
  return <form className="verification-panel" onSubmit={submit}><div className="verification-title"><span className="verification-icon">{password?'⌁':'✉'}</span><div><b>{password?'输入抖音登录密码':'短信验证码验证'}</b><small>{password?'请在此输入抖音账号密码，提交一次即可。':scan?.masked_phone?`验证码将发送至 ${scan.masked_phone}`:'请填写抖音页面要求的验证码。'}</small></div></div><label className="verification-field"><span>{password?'登录密码':'验证码'}</span><input className="input" type={password?'password':'text'} inputMode={password?'text':'numeric'} autoComplete={password?'current-password':'one-time-code'} value={value} onChange={e=>setValue(e.target.value)} placeholder={password?'输入密码':'输入验证码'}/></label><div className="verification-actions"><Button type="submit" disabled={sending||!value.trim()}>{sending?'提交中…':'提交验证'}</Button>{!password&&<span className="verification-hint">{scan?.resend_after_seconds?`${scan.resend_after_seconds}s 后可重试`:scan?.verification_sent?'验证码已发送':'等待抖音发送验证码'}</span>}</div>{notice&&<div className="inline-feedback">{notice}</div>}</form>;
}

function CloudInteraction({scan,data}) {
  const [value,setValue]=useState(''); const [busy,setBusy]=useState(false); const [status,setStatus]=useState('');
  if (scan?.needs_verification) return null;
  const submit=async e=>{
    e.preventDefault(); if(!value.trim()||busy||!scan?.id)return;
    setBusy(true); setStatus('正在发送到云端，请勿重复提交…');
    try {
      const requestId=interactionRequestId();
      const fd=new FormData(); fd.append('csrf_token',data?.csrf||''); fd.append('kind','text'); fd.append('request_id',requestId); fd.append('text',value.trim());
      const accepted=await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd});
      setValue('');
      if(accepted?.status_url){
        let state=accepted; let tries=0;
        while(tries++<16 && ['queued','processing'].includes(state?.status)) { await new Promise(resolve=>setTimeout(resolve,650)); state=await api(state.status_url); }
        setStatus(state?.status==='delivered'?'云端已确认本次输入':state?.status==='failed'?'云端未接受本次输入，请查看画面后重试':'已提交，等待云端回执');
      } else setStatus('已提交，等待云端回执');
    } catch(err){ setStatus(err.message||'云端输入未送达'); }
    finally{setBusy(false)}
  };
  return <form className="cloud-interaction" onSubmit={submit}><div className="cloud-interaction-copy"><span className="eyebrow">CLOUD INPUT</span><span>点击上方云端画面选中输入框，或直接在这里输入后发送。</span></div><div className="cloud-input-row"><input className="input" value={value} onChange={e=>setValue(e.target.value)} placeholder="验证码、密码或抖音页面提示内容" autoComplete="one-time-code"/><Button type="submit" disabled={busy||!value.trim()}>{busy?'发送中…':'发送到云端'}</Button></div>{status&&<p className="cloud-interaction-status" role="status">{status}</p>}</form>;
}

function CloudFrame({scan,data,onError,onReady}) {
  const [frame,setFrame]=useState(()=>createCloudFrameState(scan?.id)); const [pointer,setPointer]=useState({x:50,y:50}); const [keyboardActive,setKeyboardActive]=useState(false); const preview=scan?.id==='preview-scan';
  const state=frame.status; const attempt=frame.attempt; const frameError=frame.error;
  useEffect(()=>{setFrame(createCloudFrameState(scan?.id));setPointer({x:50,y:50});setKeyboardActive(false)},[scan?.id]);
  const sendClick=async (x,y)=>{if(!scan?.id||state!=='ready'||frameError)return;const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','click');fd.append('x',String(Math.max(0,Math.min(1,x/100))));fd.append('y',String(Math.max(0,Math.min(1,y/100))));try{await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd})}catch(err){onError?.(err.message)}};
  const click=async e=>{const rect=e.currentTarget.getBoundingClientRect();if(!rect.width||!rect.height)return;const x=Math.max(0,Math.min(100,(e.clientX-rect.left)/rect.width*100));const y=Math.max(0,Math.min(100,(e.clientY-rect.top)/rect.height*100));setPointer({x,y});await sendClick(x,y)};
  const keyDown=e=>{
    if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Enter',' ','Spacebar'].includes(e.key))return;
    e.preventDefault();
    if(e.key==='Enter'||e.key===' '||e.key==='Spacebar'){sendClick(pointer.x,pointer.y);return;}
    const step=e.shiftKey?10:5;
    const next={x:pointer.x,y:pointer.y};
    if(e.key==='ArrowUp')next.y=Math.max(0,next.y-step);
    if(e.key==='ArrowDown')next.y=Math.min(100,next.y+step);
    if(e.key==='ArrowLeft')next.x=Math.max(0,next.x-step);
    if(e.key==='ArrowRight')next.x=Math.min(100,next.x+step);
    setPointer(next);
  };
  useEffect(()=>{
    if(preview||state==='ready'||frameError||!scan?.id)return;
    const timer=window.setTimeout(()=>setFrame(current=>frameLoadError(current,{remainingSeconds:scan.remaining_seconds})),Math.min(5000,1600+attempt*500));
    return ()=>window.clearTimeout(timer);
  },[preview,state,frameError,attempt,scan?.id,scan?.remaining_seconds]);
  if(preview) return <div className="scan-stage frame-ready"><div className="preview-qr" aria-label="预览二维码"><span>DYFIRE</span><b>扫码预览</b></div></div>;
  const revision=scan?.frame_revision||'';
  const retry=()=>setFrame(current=>retryCloudFrame(current));
  const sessionStatus=String(scan?.status||'').toLowerCase();
  const sessionActive=!['succeeded','failed','expired','cancelled'].includes(sessionStatus)&&Number(scan?.remaining_seconds??1)>0;
  const overlay=cloudFrameOverlay(frame);
  const frameClass=overlay.kind==='error'?'scan-frame-error':state==='loading'&&attempt>3?'scan-frame-waiting':'';
  return <div className={`scan-stage ${state==='ready'?'frame-ready':''} ${frameClass}`}><img className="scan-frame-image" onLoad={()=>{setFrame(current=>frameLoaded(current));onReady?.();document.body.dataset.dyfireFrameReady='true';document.dispatchEvent(new CustomEvent('dyfire-frame-ready'))}} onError={()=>setFrame(current=>frameLoadError(current,{remainingSeconds:scan?.remaining_seconds}))} onClick={click} onKeyDown={keyDown} onFocus={()=>setKeyboardActive(true)} onBlur={()=>setKeyboardActive(false)} role="button" tabIndex="0" aria-label="抖音云端登录页面。使用方向键移动远程点击位置，按 Enter 或空格点击" aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Enter Space" src={`/accounts/scan/${scan.id}/qr?attempt=${attempt}${revision?`&revision=${encodeURIComponent(revision)}`:''}`} alt="抖音云端登录页面"/>{keyboardActive&&<span className="scan-keyboard-pointer" aria-hidden="true" style={{left:`${pointer.x}%`,top:`${pointer.y}%`}}/>}<div className="scan-loading-overlay" aria-live="polite">{overlay.kind==='error'?<><b>{overlay.title}</b><small>{overlay.detail}</small>{overlay.title!=='二维码已失效'&&<Button type="button" variant="quiet" onClick={retry}>重试加载画面</Button>}</>:<><span className="scan-loading-spinner"/><b>{overlay.title}</b><small>{overlay.detail|| (sessionActive?'云端容器启动后会自动显示，请稍候。':'正在确认登录会话状态，请稍候。')}</small></>}</div></div>;
}

function BindingProgress({scan}) { const status=String(scan?.status||'').toLowerCase(); const current=scan?.status==='succeeded'?4:scan?.needs_verification?3:['queued','starting','initializing','pending','created'].includes(status)?1:2; const labels=['准备环境','扫码确认','验证输入','绑定完成']; return <div className="scan-progress scan-progress-four" aria-label={`绑定进度：${labels[current-1]}`}>{labels.map((label,index)=><React.Fragment key={label}><span className={`scan-progress-step ${index+1<current?'complete':''} ${index+1===current?'active':''}`}><i className="scan-progress-dot"/><span>{label}</span></span>{index<labels.length-1&&<span className={`scan-progress-line ${index+1<current?'complete':''}`} aria-hidden="true"/>}</React.Fragment>)}</div>; }

function Accounts({data,onRefresh}) {
  const [scan,setScan]=useState(null); const [error,setError]=useState(''); const [busy,setBusy]=useState(false); const [frameReady,setFrameReady]=useState(false); const navigate=useNavigate();
  const accounts=Array.isArray(data?.accounts)?data.accounts:[];
  const tasks=Array.isArray(data?.tasks)?data.tasks:[];
  const refreshScan=async()=>{if(!scan?.id)return;for(let attempt=0;attempt<8;attempt++){await new Promise(resolve=>setTimeout(resolve,attempt?350:120));try{const next=await api(`/accounts/scan/${scan.id}`);if(next&&typeof next==='object')setScan(current=>current?.id===scan.id?next:current)}catch(_error){break}}};
  useEffect(()=>{if(!scan?.id)return;let timer;let failures=0;let cancelled=false;const poll=async()=>{try{const next=await api(`/accounts/scan/${scan.id}`);if(cancelled)return;failures=0;if(!next||typeof next!=='object'){setError('登录状态暂时没有返回，请重新绑定');setScan(null);return}setScan(next);if(!['succeeded','failed','expired','cancelled'].includes(next.status))timer=setTimeout(poll,1500);else if(next.status==='succeeded')onRefresh()}catch(e){if(cancelled)return;failures+=1;setError(failures>=8?`暂时无法读取扫码状态，正在继续重试：${e.message}`:`连接短暂中断，${Math.min(failures*2,10)} 秒后自动重试`);timer=setTimeout(poll,Math.min(failures*2000,10000))}};timer=setTimeout(poll,500);return()=>{cancelled=true;clearTimeout(timer)}},[scan?.id,onRefresh]);
  const start=async(accountId='')=>{setBusy(true);setError('');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');if(accountId)fd.append('account_id',accountId);const v=await api(accountId?'/accounts/recovery':'/accounts/scan',{method:'POST',body:fd});if(!v||typeof v!=='object')throw new Error('绑定服务没有返回登录状态');setScan(v)}catch(e){setError(e.message)}finally{setBusy(false)}};
  const terminal=scan&&['failed','expired','cancelled'].includes(scan.status);
  const requestHealthCheck=async accountId=>{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');try{await api(`/accounts/${accountId}/health-check`,{method:'POST',body:fd});setError('已安排登录复核，稍后会自动更新状态')}catch(e){setError(e.message)}};
  const openAccountCheck=account=>{const health=account?.health||{};const blocked=health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(account?.validation_state);if(blocked)start(account.id);else requestHealthCheck(account.id)};
  const recoveryError=error&&(/未找到抖音账号|账号不存在|404/.test(error));
  useEffect(()=>{if(recoveryError)setError('账号复核失败：原账号可能已删除或权限已变化，请重新绑定。')},[recoveryError]);
  useEffect(()=>{setFrameReady(false);document.body.dataset.dyfireFrameReady='false'},[scan?.id]);
  useEffect(()=>{const ready=()=>setFrameReady(true);document.addEventListener('dyfire-frame-ready',ready);return()=>document.removeEventListener('dyfire-frame-ready',ready)},[]);
  return <><PageHeader eyebrow="03 / DOUYIN ACCOUNTS" title="抖音账号" text="集中管理登录状态、资料同步与安全复核。" actions={<Button onClick={()=>start()} disabled={busy}>{busy?'正在准备…':'绑定抖音账号 ↗'}</Button>}/>{error&&<div className="inline-feedback danger-text">{error}</div>}<div className="account-grid">{accounts.length?accounts.map(a=>{const name=String(a?.name||'抖音账号'); const profile=a?.profile||{}; const health=a?.health||{}; const taskCount=data?.resource_limits?.task_counts_by_account?.[a.id]??tasks.filter(t=>t?.accountId===a?.id).length; return <Card className="account-card" id={`account-${a.id||name}`} key={a.id||name}><div className="account-card-head"><span className="avatar avatar-large">{profile.avatar_url?<img src={profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><h2>{name}</h2><small>{profile.nickname||'抖音账号'} · 绑定于此工作区</small>{health.reason&&<small className="account-health-reason">{health.reason}</small>}</div><Badge tone={(health.state==='valid'||a.validation_state==='valid')?'good':'warn'}>{health.label||'等待复核'}</Badge></div><div className="account-card-meta"><span>登录状态<strong>{health.label||'未复核'}</strong></span><span>续火任务<strong>{taskCount} 条</strong></span></div><div className="account-card-actions"><Button variant="quiet" onClick={()=>navigate('/studio')}>创建任务 ↗</Button><Button variant="quiet" onClick={()=>{const blocked=health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(a.validation_state);blocked?start(a.id):requestHealthCheck(a.id)}}>{(health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(a.validation_state))?'打开验证':'复核状态'}</Button></div></Card>}):<Empty title="还没有绑定抖音账号" text="扫码后按抖音 App 提示确认登录。" action={<Button onClick={()=>start()}>开始绑定</Button>}/>}</div><Card className="account-guide"><span className="eyebrow">ACCOUNT CARE</span><h2>保持账号稳定</h2><p>系统会在发送前复核账号状态；遇到抖音安全验证时，会暂停相关计划并在这里显示处理入口。</p><div className="guide-steps"><span><b>01</b>扫码确认</span><span><b>02</b>完成验证</span><span><b>03</b>自动复核</span></div></Card><Drawer open={!!scan} onClose={()=>setScan(null)} title="绑定抖音账号" eyebrow="SECURE LOGIN" wide centered><div className="scan-panel">{scan?.status==='succeeded'?<><div className="success-mark">✓</div><h3>绑定成功</h3><p>抖音账号已安全保存，可以创建续火任务。</p><Button onClick={()=>{setScan(null);navigate('/studio')}}>去创建任务 ↗</Button></>:terminal?<><div className="success-mark failure-mark">!</div><h3>本次绑定未完成</h3><p>{scan?.message||'二维码已失效，请重新发起绑定。'}</p><Button onClick={()=>start()}>重新绑定</Button></>:<><BindingProgress scan={scan}/><CloudFrame scan={scan} data={data} onError={setError} onInteraction={refreshScan}/><h3>{scan?.message||'请使用抖音 App 扫码并在手机确认'}</h3><p>{scan?.needs_verification?'抖音已要求额外验证，请在下方完成一次提交。':'二维码只在当前登录窗口有效，完成确认后页面会自动更新状态。'}</p><CloudInteraction scan={scan} data={data} onInteraction={refreshScan}/><ScanVerification scan={scan} data={data} setScan={setScan}/><Button variant="quiet" onClick={()=>{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');api(`/accounts/scan/${scan?.id}/cancel`,{method:'POST',body:fd});setScan(null)}}>取消绑定</Button></>}</div></Drawer></>;
}

function Runs({data}) {
  const [rows,setRows]=useState(asList(data?.runs));
  const [filter,setFilter]=useState('');
  const [expanded,setExpanded]=useState(null);
  useEffect(()=>{api(`/api/studio/runs?status=${filter}`).then(v=>setRows(asList(v?.items))).catch(()=>{})},[filter]);
  const tone=r=>r.status==='success'?'good':r.status==='failed'?'danger':r.status==='submitted_unconfirmed'?'warn':r.status==='running'?'running':'neutral';
  const runFilterItems=[{value:'',label:'全部'},{value:'success',label:'已发出'},{value:'failed',label:'未发出'},{value:'submitted_unconfirmed',label:'待核实'}];
  return <><PageHeader eyebrow="04 / DELIVERY LOG" title="执行记录" text="每次发送都保留可核对的状态、阶段和尝试历史。"/><div className="toolbar runs-toolbar"><RubberSegment className="admin-segment" items={runFilterItems} value={filter} onChange={setFilter} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="sm" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="执行记录筛选"/><span className="runs-count">{rows.length} 条记录</span></div><Card className="runs-card">{rows.length?<div className="run-list">{rows.map(r=>{const open=expanded===r.id;const detailId=`run-detail-${r.id}`;return <div className={`run-row ${open?'is-expanded':''}`} key={r.id} role="button" tabIndex="0" aria-expanded={open} aria-controls={detailId} aria-label={`${r.name||'执行记录'}，${r.label||r.status||'状态未知'}。按 Enter 或空格查看详情`} onClick={()=>setExpanded(open?null:r.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setExpanded(open?null:r.id)}}}><span className={`run-dot ${r.status}`} aria-hidden="true"/><div className="grow"><b>{r.name}</b><small>{r.time} · {r.kind||r.stage}</small><div id={detailId} className="run-detail" hidden={!open}><span>执行阶段：{r.stage||'—'}</span><span>{r.error||r.guidance?.text||'本次执行完成，未发现异常。'}</span>{r.timeline?.map(item=><span key={`timeline-${item.sequence}`}><b>{item.occurred_at} · {item.stage}</b> {item.detail||item.event}</span>)}{r.attempts?.map(a=><span key={a.number}>第 {a.number} 次：{a.reason||a.stage||'—'}</span>)}</div></div><Badge tone={tone(r)}>{r.label||r.status}</Badge><span className="run-error">{r.error||r.guidance?.text||'计划执行正常'}</span></div>})}</div>:<Empty title="还没有执行记录" text="任务开始运行后，结果会出现在这里。"/>}</Card></>;
}

function TaskEditDrawer({task, data, open, onClose, onRefresh}) {
  const [time,setTime]=useState('09:30');
  const [kind,setKind]=useState('douyin_sticker');
  const [message,setMessage]=useState('');
  const [status,setStatus]=useState('');
  const [saving,setSaving]=useState(false);
  useEffect(()=>{
    if(!task)return;
    setTime(task.time||'09:30');
    setKind(task.kind==='文字消息'?'text':'douyin_sticker');
    setMessage(task.message||'');
    setStatus('');
    setSaving(false);
  },[task?.id]);
  const save=async e=>{
    e.preventDefault();
    setSaving(true); setStatus('正在保存…');
    const fd=new FormData();
    fd.append('csrf_token',data?.csrf||'');
    fd.append('account_id',task?.accountId||'');
    fd.append('target_name',task?.name||'');
    fd.append('target_sec_uid',task?.sec_uid||'');
    fd.append('send_time',time);
    fd.append('message_kind',kind);
    fd.append('message_template',kind==='text'?message:'续火花');
    try{
      await api(`/tasks/${task?.id}/edit`,{method:'POST',body:fd});
      setStatus('已保存');
      await onRefresh();
      setTimeout(onClose, 600);
    }catch(err){setStatus(err.message);}
    finally{setSaving(false);}
  };
  if(!task) return null;
  const name=displayName(task?.name,'未命名好友');
  return (
    <Drawer open={open} onClose={onClose} title={`编辑 ${name}`} eyebrow="TASK SETTINGS" centered>
      <div className="drawer-content">
        <form className="form-stack" onSubmit={save}>
          <div className="edit-target">
            <span className="avatar avatar-large">{task?.avatar?<img src={task.avatar} alt=""/>:initial(task?.name,'未')}</span>
            <div><b>{name}</b><small>目标好友 · 账号任务</small></div>
          </div>
          <label>每日发送时间
            <input className="input" type="time" value={time} onChange={e=>setTime(e.target.value)}/>
          </label>
          <label>消息类型
            <select className="input" value={kind} onChange={e=>setKind(e.target.value)}>
              <option value="douyin_sticker">续火花表情</option>
              <option value="text">文字消息</option>
            </select>
          </label>
          {kind==='text'&&<label>发送内容
            <textarea className="input textarea" value={message} onChange={e=>setMessage(e.target.value)} placeholder="写一句日常问候"/>
          </label>}
          {status&&<div className="inline-feedback">{status}</div>}
          <Button type="submit" disabled={saving}>{saving?'正在保存…':'保存任务 ↗'}</Button>
        </form>
      </div>
    </Drawer>
  );
}

function TaskEdit({data,id,onRefresh}) {
  const navigate=useNavigate();
  // Render Studio with the edit drawer pre-opened for this task id
  return <Studio data={data} onRefresh={onRefresh} preEditId={id} onEditClose={()=>navigate('/studio')}/>;
}

function Notifications({data}) {
  const [state,setState]=useState(null); const [status,setStatus]=useState(''); const [email,setEmail]=useState(''); const [enabled,setEnabled]=useState(false); const [events,setEvents]=useState([]); const [quietEnabled,setQuietEnabled]=useState(false); const [quietStart,setQuietStart]=useState(23); const [quietEnd,setQuietEnd]=useState(8); const [interval,setInterval]=useState(60); const [code,setCode]=useState(''); const [busy,setBusy]=useState(false);
  const load=async()=>{const v=await api('/api/notifications');setState(v||{});setEmail(v?.preference?.email||'');setEnabled(!!v?.preference?.enabled);setEvents(asList(v?.policy?.events));setQuietEnabled(!!v?.policy?.quiet_enabled);setQuietStart(Number(v?.policy?.quiet_start??23));setQuietEnd(Number(v?.policy?.quiet_end??8));setInterval(Number(v?.policy?.interval_minutes??60));return v};
  useEffect(()=>{load().catch(e=>setStatus(e.message))},[]);
  const csrf=()=>state?.csrf||data?.csrf||'';
  const save=async()=>{setBusy(true);setStatus('正在保存…');try{const result=await api('/notifications/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),email,enabled})});setStatus(result?.message||'接收设置已保存');await load()}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const sendVerification=async()=>{setBusy(true);setStatus('正在保存邮箱并发送验证码…');try{if(email.trim().toLowerCase()!==(state?.preference?.email||'').trim().toLowerCase()){await api('/notifications/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),email,enabled:false})});await load()}const result=await api('/notifications/verify-send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf()})});setStatus(result?.message||'验证邮件已排队')}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const verify=async e=>{e.preventDefault();setBusy(true);setStatus('正在验证…');try{const result=await api('/notifications/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),code:code.trim()})});setStatus(result?.message||'邮箱验证成功');setCode('');await load()}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const savePolicy=async()=>{setBusy(true);setStatus('正在保存通知策略…');try{const result=await api('/notifications/policy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),events,quiet_start:Number(quietStart),quiet_end:Number(quietEnd),quiet_enabled:quietEnabled,interval_minutes:Number(interval)})});setStatus(result?.message||'通知偏好已保存')}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const hours=Array.from({length:24},(_,hour)=><option key={hour} value={hour}>{String(hour).padStart(2,'0')}:00</option>);
  return <><PageHeader eyebrow="07 / NOTIFICATIONS" title="通知设置" text="管理接收邮箱、提醒类型、免打扰时段和重复提醒频率。"/><div className="simple-grid"><Card><span className="eyebrow">DELIVERY ADDRESS</span><h2>接收邮箱</h2><div className="form-stack"><label>邮箱地址<input className="input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email"/></label><label className="check-line"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)} disabled={!state?.preference?.verified}/>接收通知邮件</label>{state?.preference?.verified?<Badge tone="good">邮箱已验证</Badge>:<small className="muted">保存邮箱后发送验证码，输入验证码即可完成验证。</small>}{!state?.mail_ready&&<small className="muted">邮件服务尚未启用，请联系管理员。</small>}{!state?.preference?.verified&&<div className="form-grid"><Button variant="quiet" disabled={busy||!email||!state?.mail_ready} onClick={sendVerification}>发送验证邮件</Button><form className="form-stack" onSubmit={verify}><label>邮箱验证码<input className="input" value={code} onChange={e=>setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6 位验证码"/></label><Button type="submit" disabled={busy||code.trim().length<4}>验证邮箱</Button></form></div>}{status&&<div className="inline-feedback" role="status">{status}</div>}<Button disabled={busy||!email} onClick={save}>{busy?'请稍候…':'保存邮箱设置'}</Button></div></Card><Card><span className="eyebrow">DELIVERY POLICY</span><h2>提醒偏好</h2><div className="form-stack">{[['account_alert','账号需要验证'],['account_recovered','账号恢复正常'],['task_failed','任务执行异常'],['expiry','订阅即将到期']].map(([id,label])=><label className="check-line" key={id}><input type="checkbox" checked={events.includes(id)} onChange={e=>setEvents(v=>e.target.checked?[...new Set([...v,id])]:v.filter(x=>x!==id))}/>{label}</label>)}<label className="check-line"><input type="checkbox" checked={quietEnabled} onChange={e=>setQuietEnabled(e.target.checked)}/>开启每日免打扰</label><div className="form-grid"><label>开始时间<Select className="input" value={quietStart} onChange={e=>setQuietStart(e.target.value)}>{hours}</Select></label><label>结束时间<Select className="input" value={quietEnd} onChange={e=>setQuietEnd(e.target.value)}>{hours}</Select></label></div><label>重复提醒间隔<Select className="input" value={interval} onChange={e=>setInterval(e.target.value)}><option value={15}>15 分钟</option><option value={60}>1 小时</option><option value={180}>3 小时</option></Select></label><small className="muted">免打扰时段内延迟提醒；验证邮件不受此设置影响。</small><Button variant="quiet" disabled={busy} onClick={savePolicy}>保存提醒偏好</Button></div></Card></div></>;
}

function MembershipPlanNotice({membership, compact=false}) {
  const pending = pendingMembershipPlan(membership);
  const overLimit = membershipOverLimit(membership);
  const currentLimit = membershipTaskLimitLabel(membership);
  const pendingLimit = pending ? membershipTaskLimitLabel(pending) : '';
  const pendingDate = pending?.valid_from || pending?.starts_at || pending?.effective_at;
  const pendingUntil = pending?.valid_until || pending?.ends_at;
  if (!pending && !overLimit) return null;
  return <div className={`membership-plan-notices${compact?' is-compact':''}`}>
    {pending&&<div className="membership-plan-notice pending" role="status">
      <span className="membership-plan-notice-mark" aria-hidden="true">↗</span>
      <div><b>{pendingPlanIsDowngrade(membership,pending)?'降档已排队':'下期套餐已安排'}</b>
        <p>当前套餐保持至 {membershipDate(membership?.valid_until)}；{pendingDate?`下期从 ${membershipDate(pendingDate)} 起`:'到当前套餐到期后'}切换为 {pendingLimit}。</p>
        {pendingUntil&&<small>下期有效期至 {membershipDate(pendingUntil)}</small>}
      </div>
    </div>}
    {overLimit>0&&<div className="membership-plan-notice over-limit" role="alert">
      <span className="membership-plan-notice-mark" aria-hidden="true">!</span>
      <div><b>当前任务超出套餐额度</b><p>已有 {membershipTaskUsage(membership) ?? '—'} 个启用任务，当前上限为 {currentLimit}，超出 {overLimit} 个。</p><small>不会删除现有任务；请暂停或删除多余任务，或续费更高套餐后再新增。</small></div>
    </div>}
  </div>;
}

function SimplePage({data,type}) {
  const navigate=useNavigate(); const [notice,setNotice]=useState(''); const [code,setCode]=useState(''); const [redeeming,setRedeeming]=useState(false); const [remote,setRemote]=useState(null); const [loading,setLoading]=useState(false); const [busy,setBusy]=useState(false); const [supportOpen,setSupportOpen]=useState(false);
  const map={subscription:['05 / MEMBERSHIP','会员订阅','兑换码开通、查看有效期与任务额度。'], 'account-center':['06 / ACCOUNT','账户中心','管理平台账号和当前登录会话。'],attention:['08 / ATTENTION','待处理事项','需要你确认或处理的账号与执行记录。'],help:['09 / GUIDE','使用帮助','绑定、创建计划与发送结果核对。']}; const [ey,title,text]=map[type];
  const load=async()=>{setLoading(true);try{setRemote(await api(type==='subscription'?'/api/subscription':type==='account-center'?'/api/account-center':'/api/attention'))}catch(e){setNotice(e.message)}finally{setLoading(false)}};
  useEffect(()=>{if(['subscription','account-center','attention'].includes(type))load()},[type]);
  const membership=remote?.membership||data.membership||{};
  const redeem=async e=>{e.preventDefault();if(!code.trim())return setNotice('请输入兑换码');setRedeeming(true);setNotice('正在验证兑换码…');try{const value=await api('/api/subscription/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||'',code:code.trim()})});if(!value?.ok)throw new Error(value?.message||'兑换失败');setCode('');setNotice(value.message||'兑换成功，订阅额度已更新。');await load()}catch(error){setNotice(error.message||'兑换码无效或已使用')}finally{setRedeeming(false)}};
  const revokeSessions=async()=>{if(!await confirmModal({title:'退出其他设备',eyebrow:'ACCOUNT SECURITY',message:'当前设备会保持登录，其他网站登录会话将立即失效。'}))return;setBusy(true);try{const value=await api('/api/account-center/revoke-sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||''})});setNotice(value?.message||'其他登录会话已退出。');await load()}catch(e){setNotice(e.message)}finally{setBusy(false)}};
  const markRead=async itemId=>{setBusy(true);try{const value=await api('/api/attention/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||'',item_id:itemId})});setRemote(v=>({...v,items:value.items||[]}))}catch(e){setNotice(e.message)}finally{setBusy(false)}};
  const date=value=>value?new Date(value).toLocaleString('zh-CN',{dateStyle:'medium',timeStyle:'short'}):'—';
  const quickLinks=<div className="quick-links"><Link to="/dashboard">今日总览 ↗</Link><Link to="/accounts">抖音账号 ↗</Link><Link to="/studio">续火任务 ↗</Link><Link to="/runs">执行记录 ↗</Link></div>;
  let body;
  if(type==='subscription') body=<><Card><span className="eyebrow">PLAN STATUS</span><h2>{membership.status_label||'尚未开通'}</h2><p>{membership.active?`有效期至 ${date(membership.valid_until)}。已绑定 ${membership.account_count??data.accounts?.length??0} 个账号，启用任务 ${membershipTaskUsage(membership)??membership.task_count??data.tasks?.length??0} / ${membership.task_limit_label||membership.max_tasks||'不限'}。`:'联系客服购买后获取兑换码，或直接输入已有兑换码。'}</p><MembershipPlanNotice membership={membership}/><form className="redeem-form" onSubmit={redeem}><label className="auth-field"><span>兑换码</span><input className="input redemption-input" value={code} onChange={e=>setCode(e.target.value)} placeholder="输入管理员提供的兑换码" autoComplete="off" disabled={redeeming}/></label><Button type="submit" disabled={redeeming}>{redeeming?'正在兑换…':'兑换订阅'}</Button></form>{notice&&<div className="inline-feedback" role="status">{notice}</div>}<button type="button" className="support-inline-link" onClick={()=>setSupportOpen(true)}>没有兑换码？联系客服购买并开通</button></Card><Card><span className="eyebrow">PLANS</span><h2>月度套餐</h2><div className="form-stack">{(remote?.plans||data.plans||[]).map(plan=><div className="product-row" key={plan.id}><div><b>{plan.name} · ¥{plan.price_yuan} / 月</b><p>{plan.task_limit_label}</p></div></div>)}</div></Card><ContactSupportCard onOpen={()=>setSupportOpen(true)}/><Card><span className="eyebrow">HISTORY</span><h2>开通记录</h2>{remote?.grants?.length?<div className="form-stack">{remote.grants.map(grant=><div className="product-row" key={grant.id}><div><b>{grant.source==='redemption_code'?'兑换码开通':'管理员开通'}</b><p>{date(grant.valid_from)} 至 {date(grant.valid_until)}</p></div></div>)}</div>:<p className="muted">{loading?'正在读取记录…':'暂无开通记录。'}</p>}</Card></>;
  else if(type==='account-center') body=<><Card><span className="eyebrow">PROFILE & SECURITY</span><h2>{data.user?.name||'账户'}</h2><p>账户密码和网站登录会话由服务器保护。退出网站会话不会影响云端抖音账号和续火任务。</p><div className="quick-links"><Link to="/change-password">修改网站密码 ↗</Link><Link to="/notifications">通知设置 ↗</Link><Link to="/subscription">查看会员 ↗</Link></div><form method="post" action="/logout"><input type="hidden" name="csrf_token" value={remote?.csrf||data.csrf||''}/><Button type="submit" variant="quiet">退出当前设备</Button></form></Card><Card><div className="section-head"><div><span className="eyebrow">ACTIVE SESSIONS</span><h2>有效登录会话</h2></div><Button variant="quiet" disabled={busy||loading||!(remote?.sessions||[]).some(s=>!s.current)} onClick={revokeSessions}>退出其他设备</Button></div>{remote?.sessions?.length?<div className="form-stack">{remote.sessions.map(session=><div className="product-row" key={session.id}><div><b>{session.current?'当前设备':'其他登录设备'}</b><p>登录于 {date(session.created_at)} · 到期 {date(session.expires_at)}</p></div>{session.current&&<Badge tone="good">正在使用</Badge>}</div>)}</div>:<p className="muted">{loading?'正在读取会话…':'没有有效会话。'}</p>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}</Card></>;
  else if(type==='attention') {const items=asList(remote?.items);body=<><Card><div className="section-head"><div><span className="eyebrow">INBOX</span><h2>站内通知 · {items.filter(item=>!item.read).length} 条未读</h2></div><Button variant="quiet" disabled={busy||!items.some(item=>!item.read)} onClick={()=>markRead('all')}>全部标为已读</Button></div><p className="muted-copy">同一任务的同类异常会合并展示。标为已读不会更改执行结果，也不会触发重发。</p>{items.length?<div className="form-stack">{items.map(item=><article className={`product-row ${item.read?'is-read':''}`} key={item.id}><div className="grow"><b>{item.title}</b><p>{item.detail}</p><small>{date(item.at)} · {item.read?'已读':'未读'}</small></div><div className="row-actions"><Link to={item.url}>查看详情 ↗</Link>{!item.read&&<Button variant="quiet" disabled={busy} onClick={()=>markRead(item.id)}>标为已读</Button>}</div></article>)}</div>:<p className="muted">{loading?'正在读取待处理事项…':'暂时没有新的通知。'}</p>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}</Card><Card><span className="eyebrow">QUICK LINKS</span>{quickLinks}</Card></>}
  else body=<><Card><span className="eyebrow">FIRST STEPS</span><h2>第一次使用</h2><ol><li>开通会员，确认账号和任务额度。</li><li>绑定抖音，在手机上确认扫码，并完成抖音要求的安全验证。</li><li>读取好友，可按昵称或备注搜索并批量选择。</li><li>确认北京时间、消息类型后创建每日计划；多位好友会自动错开时间。</li><li>查看执行记录。待核实的消息请先到抖音聊天确认，不要直接补发。</li></ol><Button variant="quiet" onClick={()=>navigate('/accounts')}>去绑定账号 ↗</Button></Card><Card><span className="eyebrow">COMMON QUESTIONS</span><h2>常见问题</h2><div className="form-stack"><details><summary>扫码慢、密码验证或手机号验证怎么办？</summary><p>先查看排队和验证提示，按抖音当前页面要求操作并等待结果。不要连续提交验证码。云端出现冷却提示时等待平台解除。</p></details><details><summary>“已发出”“待核实”“未发出”有什么区别？</summary><p>已发出表示系统确认发送完成；待核实表示已提交但未取得可靠确认，请先核对聊天记录；未发出表示这次没有发出去，可根据执行详情决定是否补发。</p></details><details><summary>账号恢复后会补发错过的消息吗？</summary><p>不会。恢复后继续执行后续计划，不补发历史消息。手动暂停的任务需要你手动恢复。</p></details><details><summary>续费和任务额度如何计算？</summary><p>未到期时续费从原到期时间顺延；已到期时从本次开通时间起算。到期后可以查看原有数据，但任务暂停执行。</p></details><details><summary>反馈问题时需要提供什么？</summary><p>通过原购买渠道联系管理员，说明用户名、发生时间、页面、期望与实际结果；执行问题请附诊断编号。不要发送密码、验证码、登录 Cookie 或邮件授权码。</p><Link to="/attention">查看待处理事项 ↗</Link></details></div></Card></>;
  return <><PageHeader eyebrow={ey} title={title} text={text} actions={['subscription','account-center','attention'].includes(type)?<Button variant="quiet" disabled={loading} onClick={load}>刷新 ↻</Button>:null}/><div className="simple-grid">{body}</div>{type==='help'&&<ContactSupportCard onOpen={()=>setSupportOpen(true)}/>}<ContactSupportModal open={supportOpen} onClose={()=>setSupportOpen(false)}/></>;
}

function Admin({data,onRefresh}) {
  const location=useLocation();
  const navigate=useNavigate();
  const path=location.pathname;
  const isTaskGroup=path.startsWith('/admin/tasks/groups/');
  const rawSection=isTaskGroup?'tasks':path.split('/')[2]||'overview';
  const baseSection=isTaskGroup||['subscriptions','tasks','customers'].includes(rawSection)?'users':rawSection==='today'?'runs':['diagnostics','audit'].includes(rawSection)?'operations':rawSection;
  const isMail=baseSection==='settings' && path.endsWith('/mail');
  const groupParts=isTaskGroup?path.split('/').slice(-2):[];
  const requestedView=new URLSearchParams(location.search).get('view');
  const [query,setQuery]=useState('');
  const [users,setUsers]=useState(data.users||[]);
  const [subscriptions,setSubscriptions]=useState(data.subscriptions||[]);
  const [taskGroups,setTaskGroups]=useState(data.groups||[]);
  const [auditRows,setAuditRows]=useState(data.audit||[]);
  const [insights,setInsights]=useState(null);
  const [selectedAudit,setSelectedAudit]=useState(null);
  const [userStatusFilter,setUserStatusFilter]=useState('');
  const [subscriptionStatusFilter,setSubscriptionStatusFilter]=useState('');
  const [taskStatusFilter,setTaskStatusFilter]=useState('');
  const [auditQuery,setAuditQuery]=useState('');
  const [insightDays,setInsightDays]=useState(7);
  const [usersPagination,setUsersPagination]=useState(null);
  const [subscriptionsPagination,setSubscriptionsPagination]=useState(null);
  const [taskGroupsPagination,setTaskGroupsPagination]=useState(null);
  const [auditPagination,setAuditPagination]=useState(null);
  const [groupTasksPage,setGroupTasksPage]=useState(1);
  const [runQuery,setRunQuery]=useState('');
  const [runStatusFilter,setRunStatusFilter]=useState('');
  const [runStartDate,setRunStartDate]=useState('');
  const [runEndDate,setRunEndDate]=useState('');
  const [runPage,setRunPage]=useState(1);
  const [runRows,setRunRows]=useState([]);
  const [runPagination,setRunPagination]=useState(null);
  const [runDay,setRunDay]=useState(rawSection==='today'?'today':requestedView==='today'?'today':'all');
  const [sendBoard,setSendBoard]=useState(null);
  const [userWorkspaceTab,setUserWorkspaceTab]=useState(rawSection==='subscriptions'?'subscriptions':rawSection==='tasks'?'tasks':requestedView==='subscriptions'||requestedView==='tasks'?requestedView:'customers');
  const [operationsTab,setOperationsTab]=useState(rawSection==='diagnostics'?'diagnostics':rawSection==='audit'?'audit':requestedView==='diagnostics'||requestedView==='audit'?requestedView:'server');
  const [selectedUsers,setSelectedUsers]=useState(new Set());
  const [selectedSubscriptions,setSelectedSubscriptions]=useState(new Set());
  const [selectedTasks,setSelectedTasks]=useState(new Set());
  const [update,setUpdate]=useState(null);
  const [diagnostics,setDiagnostics]=useState(null);
  const [groupDetail,setGroupDetail]=useState(null);
  const [selectedTask,setSelectedTask]=useState(null);
  const [userWorkspace,setUserWorkspace]=useState(null);
  const [feedback,setFeedback]=useState('');
  const [liveOperations,setLiveOperations]=useState(data.operations||{});
  const [mailConfig,setMailConfig]=useState(data.mail||{});
  const [operationsHistory,setOperationsHistory]=useState(null);
  const [generatedCodes,setGeneratedCodes]=useState([]);
  const [copiedCode,setCopiedCode]=useState('');
  const [redemptionCodes,setRedemptionCodes]=useState(data.redemptionCodes||[]);
  const [redemptionPagination,setRedemptionPagination]=useState(null);
  const [redemptionQuery,setRedemptionQuery]=useState('');
  const [redemptionStatus,setRedemptionStatus]=useState('');
  const [redemptionPage,setRedemptionPage]=useState(1);
  const settledQuery=useDebounced(query);
  const settledAuditQuery=useDebounced(auditQuery);
  const settledRunQuery=useDebounced(runQuery);
  const settledRedemptionQuery=useDebounced(redemptionQuery);
  const section=baseSection==='users'?(isTaskGroup?'task-group':userWorkspaceTab==='customers'?'users':userWorkspaceTab):baseSection==='runs'?(runDay==='today'?'today':'runs'):baseSection==='operations'?(operationsTab==='server'?'operations':operationsTab):baseSection;
  useEffect(()=>{
    if(rawSection==='subscriptions')setUserWorkspaceTab('subscriptions');
    else if(rawSection==='tasks')setUserWorkspaceTab('tasks');
    else if(rawSection==='users')setUserWorkspaceTab(requestedView==='subscriptions'||requestedView==='tasks'?requestedView:'customers');
    if(rawSection==='today')setRunDay('today');
    else if(rawSection==='runs')setRunDay(requestedView==='today'?'today':'all');
    if(rawSection==='diagnostics')setOperationsTab('diagnostics');
    else if(rawSection==='audit')setOperationsTab('audit');
    else if(rawSection==='operations')setOperationsTab(requestedView==='diagnostics'||requestedView==='audit'?requestedView:'server');
  },[rawSection,requestedView]);
  useEffect(()=>{
    if(baseSection==='users'&&userWorkspaceTab==='customers') api(`/api/admin/bootstrap?q=${encodeURIComponent(settledQuery)}&status=${encodeURIComponent(userStatusFilter)}&page=${usersPagination?.page||1}`).then(v=>{setUsers(v.users||[]);setUsersPagination(v.usersPagination||null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='users'&&userWorkspaceTab==='subscriptions') api(`/api/admin/subscriptions?q=${encodeURIComponent(settledQuery)}&status=${encodeURIComponent(subscriptionStatusFilter)}&page=${subscriptionsPagination?.page||1}`).then(v=>{setSubscriptions(v.items||[]);setSubscriptionsPagination(v.pagination||null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='users'&&userWorkspaceTab==='tasks'&&!isTaskGroup) api(`/api/admin/task-groups?q=${encodeURIComponent(settledQuery)}&status=${encodeURIComponent(taskStatusFilter)}&page=${taskGroupsPagination?.page||1}`).then(v=>{setTaskGroups(v.items||[]);setTaskGroupsPagination(v.pagination||null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='operations'&&operationsTab==='audit') api(`/api/admin/audit?q=${encodeURIComponent(settledAuditQuery)}&page=${auditPagination?.page||1}`).then(v=>{setAuditRows(v.items||[]);setAuditPagination(v.pagination||null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='codes') api(`/api/admin/redemption-codes?q=${encodeURIComponent(settledRedemptionQuery)}&status=${encodeURIComponent(redemptionStatus)}&page=${redemptionPage}`).then(v=>{setRedemptionCodes(v.items||[]);setRedemptionPagination(v.pagination||null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='runs') api(`/api/admin/runs?q=${encodeURIComponent(settledRunQuery)}&status=${encodeURIComponent(runStatusFilter)}&start=${runDay==='range'?runStartDate:''}&end=${runDay==='range'?runEndDate:''}&day=${runDay==='today'?'today':''}&page=${runPage}`).then(v=>{setRunRows(v.items||[]);setRunPagination(v.pagination||null);setSendBoard(runDay==='today'?v.today_board||null:null)}).catch(e=>setFeedback(e.message));
    if(baseSection==='overview'||baseSection==='runs') api(`/api/admin/insights?days=${insightDays}`).then(setInsights).catch(e=>setFeedback(e.message));
    if(baseSection==='operations'&&operationsTab==='diagnostics') api('/api/admin/diagnostics').then(setDiagnostics).catch(e=>setFeedback(e.message));
    if(baseSection==='operations'&&operationsTab==='server'){
      let active=true;
      const read=()=>api('/admin/operations/snapshot').then(value=>{if(active&&value)setLiveOperations(value)}).catch(e=>{if(active)setFeedback(e.message)});
      const readHistory=()=>api('/admin/operations/history?hours=24').then(value=>{if(active&&value)setOperationsHistory(value)}).catch(()=>{});
      read();
      readHistory();
      const timer=window.setInterval(read,30000);
      const historyTimer=window.setInterval(readHistory,60000);
      return()=>{active=false;window.clearInterval(timer);window.clearInterval(historyTimer)};
    }
    if(isTaskGroup && groupParts.length===2) api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(settledQuery)}&page=${groupTasksPage}`).then(setGroupDetail).catch(e=>setFeedback(e.message));
  },[settledQuery,userStatusFilter,subscriptionStatusFilter,taskStatusFilter,settledAuditQuery,insightDays,usersPagination?.page,subscriptionsPagination?.page,taskGroupsPagination?.page,auditPagination?.page,groupTasksPage,settledRunQuery,runStatusFilter,runStartDate,runEndDate,runPage,runDay,userWorkspaceTab,operationsTab,baseSection,section,isMail,isTaskGroup,path,settledRedemptionQuery,redemptionStatus,redemptionPage]);
  useEffect(()=>{
    if(section!=='settings'||isMail)return;
    let active=true;
    const read=()=>api('/api/admin/updates').then(value=>{if(active)setUpdate(value)}).catch(error=>{if(active)setFeedback(error.message)});
    read();
    const timer=window.setInterval(read,5000);
    return()=>{active=false;window.clearInterval(timer)};
  },[section,isMail]);
  useEffect(()=>{
    if(!isMail)return;
    let active=true;
    api('/api/admin/mail').then(value=>{if(active&&value)setMailConfig(value)}).catch(error=>{if(active)setFeedback(error.message)});
    return()=>{active=false};
  },[isMail]);
  useEffect(()=>{setSelectedUsers(new Set())},[query,userStatusFilter]);
  useEffect(()=>{setSelectedSubscriptions(new Set())},[query,subscriptionStatusFilter]);
  useEffect(()=>{setSelectedTasks(new Set())},[query,taskStatusFilter,path]);
  const title={overview:'业务概览',users:'用户管理',subscriptions:'用户管理',tasks:'用户管理','task-group':'用户管理',customers:'用户管理',codes:'兑换码',runs:'执行记录',today:'执行记录',operations:'运维监控',diagnostics:'运维监控',audit:'运维监控',settings:'系统设置'}[section]||'管理后台';
  const counts=data.counts||{};
  const copyRedemptionCode=async code=>{
    if(!code)return;
    try { await navigator.clipboard.writeText(code); setCopiedCode(code); setTimeout(()=>setCopiedCode(current=>current===code?'':current),1600); }
    catch { setFeedback('复制失败，请手动选择兑换码'); }
  };
  const copyAllRedemptionCodes=async()=>{
    if(!generatedCodes.length)return;
    try {
      await navigator.clipboard.writeText(generatedCodes.join('\n'));
      setCopiedCode('__all__');
      setTimeout(()=>setCopiedCode(current=>current==='__all__'?'':current),1600);
    } catch { setFeedback('复制失败，请手动选择兑换码'); }
  };
  const submit=async (path, fields)=>{
    if(path==='/admin/redemption-codes') return createCodes();
    setFeedback('正在保存…');
    const body=new URLSearchParams({csrf_token:data.csrf,...fields});
    try { const result=await api(path,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body}); if(result?.codes?.length)setGeneratedCodes(result.codes.map(item=>redemptionPlaintext(item)).filter(Boolean)); setFeedback(result?.codes?.length?'兑换码已生成':'已保存'); onRefresh(); }
    catch(e){ setFeedback(e.message); }
  };
  const createCodes=async()=>{setFeedback('正在生成兑换码…');try{const result=await api('/api/admin/redemption-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,plan_id:document.getElementById('code-plan')?.value||'',duration_days:Number(document.getElementById('code-days')?.value||30),max_tasks:Number(document.getElementById('code-tasks')?.value||10),max_accounts:Number(document.getElementById('code-accounts')?.value||1),quantity:Number(document.getElementById('code-quantity')?.value||1)})});const codes=(result.codes||[]).map(item=>redemptionPlaintext(item)).filter(Boolean);setGeneratedCodes(codes);setFeedback(codes.length?'兑换码已生成':'生成完成');onRefresh()}catch(e){setFeedback(e.message)}};
  const openUser=async user=>{setFeedback('正在读取客户工作台…');try{setUserWorkspace(await api(`/api/admin/users/${user.id}/workspace`));setFeedback('')}catch(e){setFeedback(e.message)}};
  useEffect(()=>{const match=path.match(/^\/admin\/customers\/([^/]+)$/);if(section==='users'&&match)api(`/api/admin/users/${match[1]}/workspace`).then(setUserWorkspace).catch(e=>setFeedback(e.message))},[path,section]);
  const refreshWorkspace=()=>{if(userWorkspace?.user?.id) openUser({id:userWorkspace.user.id})};
  const refreshUsers=()=>api(`/api/admin/bootstrap?q=${encodeURIComponent(query)}&status=${encodeURIComponent(userStatusFilter)}&page=${usersPagination?.page||1}`).then(v=>{setUsers(v.users||[]);setUsersPagination(v.usersPagination||null)}).catch(e=>setFeedback(e.message));
  const refreshSubscriptions=()=>api(`/api/admin/subscriptions?q=${encodeURIComponent(query)}&status=${encodeURIComponent(subscriptionStatusFilter)}&page=${subscriptionsPagination?.page||1}`).then(v=>{setSubscriptions(v.items||[]);setSubscriptionsPagination(v.pagination||null)}).catch(e=>setFeedback(e.message));
  const retryRun=async id=>{try{await api(`/api/admin/runs/${id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})});setFeedback('已安排补发，请查看后续执行记录');onRefresh();const params=new URLSearchParams({q:runQuery,status:runStatusFilter,page:String(runPage)});if(runDay==='today')params.set('day','today');if(runDay==='range'){params.set('start',runStartDate);params.set('end',runEndDate)}const value=await api(`/api/admin/runs?${params}`);setRunRows(value.items||[]);setRunPagination(value.pagination||null);setSendBoard(runDay==='today'?value.today_board||null:null)}catch(error){setFeedback(error.message)}};
  const subscriptionFor=id=>subscriptions.find(item=>item.id===id)||(data.subscriptions||[]).find(item=>item.id===id)||{};
  const toggleSelected=(setter,id)=>setter(current=>{const next=new Set(current);next.has(id)?next.delete(id):next.add(id);return next});
  const runBulk=async(path,body,success)=>{setFeedback('正在处理…');try{const result=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,...body})});setFeedback(`${success}（${result.updated||0} 项）`);setSelectedUsers(new Set());setSelectedSubscriptions(new Set());setSelectedTasks(new Set());onRefresh();if(baseSection==='users'&&userWorkspaceTab==='customers')refreshUsers();if(baseSection==='users'&&userWorkspaceTab==='subscriptions')refreshSubscriptions();if(baseSection==='users'&&userWorkspaceTab==='tasks'&&!isTaskGroup)api(`/api/admin/task-groups?q=${encodeURIComponent(query)}&status=${encodeURIComponent(taskStatusFilter)}&page=${taskGroupsPagination?.page||1}`).then(v=>{setTaskGroups(v.items||[]);setTaskGroupsPagination(v.pagination||null)});if(isTaskGroup&&groupParts.length===2)api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}`).then(setGroupDetail);}catch(error){setFeedback(error.message)}};
  const adminHeader=<PageHeader eyebrow={`ADMIN / ${section.toUpperCase()}`} title={title} text="管理员工作区 · 用户、任务和运行状态集中管理。" actions={<Button variant="quiet" onClick={onRefresh}>刷新数据 ↻</Button>}/>;
  const usersTabItems=[{value:'customers',label:'客户'},{value:'subscriptions',label:'订阅'},{value:'tasks',label:'任务'}];
  const runsTabItems=[{value:'today',label:'今日'},{value:'all',label:'全部记录'}];
  const opsTabItems=[{value:'server',label:'服务器'},{value:'diagnostics',label:'执行诊断'},{value:'audit',label:'审计日志'}];
  return <>{adminHeader}{feedback&&<div className="inline-feedback">{feedback}</div>}
    {baseSection==='users'&&<div className="admin-segment-wrap"><RubberSegment className="admin-segment" items={usersTabItems} value={userWorkspaceTab} onChange={tab=>{setUserWorkspaceTab(tab);navigate(`/admin/users?view=${tab}`)}} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="sm" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="用户管理视图"/></div>}
    {baseSection==='runs'&&<div className="admin-segment-wrap"><RubberSegment className="admin-segment" items={runsTabItems} value={runDay==='today'?'today':'all'} onChange={tab=>{setRunPage(1);setRunDay(tab==='today'?'today':'all');navigate(`/admin/runs?view=${tab}`)}} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="sm" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="执行记录视图"/></div>}
    {baseSection==='operations'&&<div className="admin-segment-wrap"><RubberSegment className="admin-segment" items={opsTabItems} value={operationsTab} onChange={tab=>{setOperationsTab(tab);navigate(`/admin/operations?view=${tab}`)}} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="sm" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="运维工作台视图"/></div>}
    {section==='overview'&&<><div className="metric-grid admin-metrics"><Metric label="用户" value={counts.users||0} detail="注册账户"/><Metric label="抖音账号" value={counts.accounts||0} detail="绑定数量"/><Metric label="续火任务" value={counts.tasks||0} detail="全部用户" tone="accent"/><Metric label="执行记录" value={counts.runs||0} detail="累计记录"/></div><AdminInsights insights={insights} days={insightDays} onDaysChange={setInsightDays} onOpenUser={openUser}/><Card><div className="section-head"><h2>最近执行</h2><Link to="/admin/runs">查看全部 ↗</Link></div><AdminRuns rows={data.recentRuns}/></Card></>}
    {section==='users'&&userWorkspaceTab==='customers'&&<Card><div className="section-head"><div><h2>客户工作台</h2><p className="muted-copy">筛选客户状态，编辑账号、订阅和任务。</p></div><span className="eyebrow">{usersPagination?.total??users.length} USERS</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{if(usersPagination?.page!==1)setUsersPagination({...usersPagination,page:1});setQuery(e.target.value)}} placeholder="搜索用户名"/><select className="input compact-search" value={userStatusFilter} onChange={e=>{if(usersPagination?.page!==1)setUsersPagination({...usersPagination,page:1});setUserStatusFilter(e.target.value)}}><option value="">全部状态</option><option value="active">正常</option><option value="disabled">已停用</option></select><span className="muted-copy">本页已选 {selectedUsers.size}</span></div>{selectedUsers.size>0&&<div className="admin-bulk-bar"><span>已选择 {selectedUsers.size} 位客户</span><Button variant="quiet" onClick={()=>runBulk('/api/admin/users/bulk',{user_ids:[...selectedUsers],action:'enable'},'已启用')}>批量启用</Button><Button variant="danger" onClick={()=>runBulk('/api/admin/users/bulk',{user_ids:[...selectedUsers],action:'disable'},'已停用')}>批量停用</Button><Button variant="quiet" onClick={()=>setSelectedUsers(new Set())}>清除选择</Button></div>}<div className="admin-table"><div className="table-head user-workbench-head"><span>选择</span><span>账号</span><span>订阅 / 资源</span><span>状态</span><span>操作</span></div>{users.map(u=>{const sub=subscriptionFor(u.id);const pending=pendingMembershipPlan(u);const overLimit=membershipOverLimit(u);return <div className="admin-row admin-user-row user-workbench-row" key={u.id}><input type="checkbox" checked={selectedUsers.has(u.id)} disabled={u.role==='admin'||u.id===data.user?.id} onChange={()=>toggleSelected(setSelectedUsers,u.id)} aria-label={`选择 ${u.username}`}/><button className="admin-cell-button" onClick={()=>openUser(u)}><b>{adminUserLabel(u)}<small>{u.role==='admin'?'管理员':'普通用户'} · 注册于 {u.created} · {u.status==='active'?'正常':'已停用'}</small></b></button><span>{u.account_count??sub.account_count??0} 个账号 · {u.task_count??sub.task_count??0} 个任务<small>{u.membership_label||sub.membership_label||'未开通订阅'}</small>{pending&&<small className="admin-pending-plan">下期 {membershipTaskLimitLabel(pending)} · {membershipDate(pending.valid_from||pending.starts_at||pending.effective_at)}</small>}{overLimit>0&&<small className="admin-over-limit">超出 {overLimit} 个启用任务</small>}</span><span className="admin-user-health"><Badge tone={u.status!=='active'?'danger':(u.account_health_status==='需处理'?'warn':'good')}>{u.status!=='active'?'用户停用':(u.account_health_label||'账号正常')}</Badge><small>今日成功 {u.today_success||0} · 失败 {u.today_failed||0}</small></span><span className="row-actions"><Button variant="quiet" onClick={()=>openUser(u)}>编辑</Button><Button variant="quiet" disabled={u.role==='admin'||u.id===data.user?.id} onClick={async()=>{try{await api(`/api/admin/users/${u.id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})});setFeedback('用户状态已更新');refreshUsers();onRefresh()}catch(e){setFeedback(e.message)}}}>{u.status==='active'?'停用':'启用'}</Button></span></div>})}</div><AdminPagination value={usersPagination} onChange={page=>setUsersPagination(p=>({...p,page}))}/></Card>}
    {section==='subscriptions'&&<Card><div className="section-head"><h2>订阅与额度工作台</h2><span className="eyebrow">{subscriptionsPagination?.total??subscriptions.length} ACCOUNTS</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{if(subscriptionsPagination?.page!==1)setSubscriptionsPagination({...subscriptionsPagination,page:1});setQuery(e.target.value)}} placeholder="搜索用户名"/><select className="input compact-search" value={subscriptionStatusFilter} onChange={e=>{if(subscriptionsPagination?.page!==1)setSubscriptionsPagination({...subscriptionsPagination,page:1});setSubscriptionStatusFilter(e.target.value)}}><option value="">全部订阅</option><option value="active">使用中</option><option value="expiring">即将到期</option><option value="expired">已过期</option><option value="inactive">未开通</option></select><span className="muted-copy">本页已选 {selectedSubscriptions.size}</span></div>{selectedSubscriptions.size>0&&<div className="admin-bulk-bar"><span>已选择 {selectedSubscriptions.size} 个订阅</span><Button variant="quiet" onClick={()=>runBulk('/api/admin/subscriptions/bulk',{user_ids:[...selectedSubscriptions],action:'extend',duration_days:30},'已批量续期 30 天')}>续期 30 天</Button><Button variant="danger" onClick={async()=>{if(await confirmModal({title:'批量撤销订阅',eyebrow:'SUBSCRIPTION WORKBENCH',message:`将撤销 ${selectedSubscriptions.size} 个有效订阅，撤销后用户将无法创建新任务。`}))runBulk('/api/admin/subscriptions/bulk',{user_ids:[...selectedSubscriptions],action:'revoke'},'已撤销订阅')}}>批量撤销</Button><Button variant="quiet" onClick={()=>setSelectedSubscriptions(new Set())}>清除选择</Button></div>}<div className="admin-table"><div className="table-head subscription-workbench-head"><span>选择</span><span>用户</span><span>状态</span><span>有效期 / 额度</span><span>操作</span></div>{subscriptions.map(u=>{const pending=pendingMembershipPlan(u);const overLimit=membershipOverLimit(u);return <div className="admin-row subscription-workbench-row" key={u.id}><input type="checkbox" checked={selectedSubscriptions.has(u.id)} disabled={u.role==='admin'} onChange={()=>toggleSelected(setSelectedSubscriptions,u.id)} aria-label={`选择 ${u.username}`}/><button className="admin-cell-button" onClick={()=>openUser(u)}><b>{adminUserLabel(u)}<small>{u.account_count} 个账号 · {u.task_count} 个任务</small></b></button><Badge tone={u.membership_status==='active'?'good':u.membership_status==='expiring'?'warn':'danger'}>{u.membership_label}</Badge><span>{u.valid_until?new Date(u.valid_until).toLocaleDateString('zh-CN'):'未开通'}<small>最多 {u.max_accounts||1} 个账号 · {u.task_limit_label||u.max_tasks||10} 个任务</small>{pending&&<small className="admin-pending-plan">下期 {membershipTaskLimitLabel(pending)} · {membershipDate(pending.valid_from||pending.starts_at||pending.effective_at)}</small>}{overLimit>0&&<small className="admin-over-limit">超出 {overLimit} 个启用任务</small>}</span><span className="row-actions"><Button variant="quiet" onClick={()=>submit(`/admin/users/${u.id}/subscription`,{duration_days:'30',max_accounts:String(u.max_accounts||1),max_tasks:String(u.max_tasks||10)})}>续期 30 天</Button><Button variant="quiet" onClick={()=>openUser(u)}>编辑</Button></span></div>})}</div><AdminPagination value={subscriptionsPagination} onChange={page=>setSubscriptionsPagination(p=>({...p,page}))}/></Card>}
    {section==='codes'&&<>
      <Card><div className="section-head"><div><span className="eyebrow">ISSUE ACCESS</span><h2>生成兑换码</h2><p className="muted-copy">未兑换的兑换码不会过期，兑换后从兑换时间开始计算有效期。</p></div></div><div className="form-grid admin-code-form"><label>套餐<select className="input" id="code-plan"><option value="">自定义额度</option>{(data.plans||[]).map(plan=><option value={plan.id} key={plan.id}>{plan.name} · ¥{plan.price_yuan}/月 · {plan.task_limit_label}</option>)}</select></label><label>有效天数<input className="input" defaultValue="30" id="code-days"/></label><label>任务额度<input className="input" defaultValue="10" id="code-tasks"/></label><label>账号额度<input className="input" defaultValue="1" id="code-accounts"/></label><label>数量<input className="input" defaultValue="1" id="code-quantity"/></label></div><Button onClick={createCodes}>生成兑换码 ↗</Button></Card>
      {generatedCodes.length>0&&<Card className="redemption-generated-card"><div className="section-head"><div><h2>刚生成的兑换码</h2><p className="muted-copy">兑换码会保存在下方记录中，可随时复制。</p></div><div className="redemption-generated-actions"><Badge tone="good">{generatedCodes.length} 个</Badge><Button variant="quiet" onClick={copyAllRedemptionCodes}>{copiedCode==='__all__'?'已复制全部':'复制全部'}</Button></div></div><div className="redemption-generated-list">{generatedCodes.map(code=><div className="redemption-generated-row" key={code}><code>{code}</code><Button variant="quiet" onClick={()=>copyRedemptionCode(code)}>{copiedCode===code?'已复制':'复制'}</Button></div>)}</div></Card>}
      <Card className="redemption-table-card"><div className="section-head"><h2>兑换码记录</h2><span className="eyebrow">{redemptionPagination?.total??redemptionCodes.length} CODES</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={redemptionQuery} onChange={e=>{setRedemptionPage(1);setRedemptionQuery(e.target.value)}} placeholder="搜索兑换码或用户"/><select className="input compact-search" value={redemptionStatus} onChange={e=>{setRedemptionPage(1);setRedemptionStatus(e.target.value)}}><option value="">全部状态</option><option value="active">未使用</option><option value="redeemed">已兑换</option><option value="revoked">已撤销</option><option value="expired">已过期</option></select></div><div className="admin-table redemption-table"><div className="table-head"><span>兑换码</span><span>状态 / 使用者</span><span>兑换日期</span><span>有效期至</span><span>额度</span></div>{redemptionCodes.map(c=>{const code=redemptionPlaintext(c);const status=c.status||'未使用';const tone=status==='未使用'||status==='待兑换'?'good':status==='已撤销'?'danger':'neutral';return <div className="admin-row" key={c.id||code}><div className="redemption-code-cell"><code>{code||'—'}</code>{code&&<Button variant="quiet" onClick={()=>copyRedemptionCode(code)}>{copiedCode===code?'已复制':'复制'}</Button>}</div><span><Badge tone={tone}>{status==='待兑换'?'未使用':status}</Badge><small>{c.username||c.used_by_username||'尚未兑换'}</small></span><time>{formatAdminDate(c.redeemed_at)}</time><time>{c.expires_at?formatAdminDate(c.expires_at):'兑换后开始计算'}</time><span>{c.max_accounts} 账号 · {c.task_limit_label||c.max_tasks} 任务</span></div>})}</div><AdminPagination value={redemptionPagination} onChange={page=>setRedemptionPage(page)}/></Card>
    </>}
    {section==='tasks'&&<Card><div className="section-head"><h2>按用户与抖音账号归组</h2><span className="eyebrow">{taskGroupsPagination?.total??taskGroups.length} GROUPS</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{if(taskGroupsPagination?.page!==1)setTaskGroupsPagination({...taskGroupsPagination,page:1});setQuery(e.target.value)}} placeholder="搜索用户、账号或好友"/><select className="input compact-search" value={taskStatusFilter} onChange={e=>{if(taskGroupsPagination?.page!==1)setTaskGroupsPagination({...taskGroupsPagination,page:1});setTaskStatusFilter(e.target.value)}}><option value="">全部任务状态</option><option value="enabled">有启用任务</option><option value="disabled">有暂停任务</option></select></div><div className="admin-table"><div className="table-head task-group-head"><span>用户 / 抖音账号</span><span>任务数</span><span>启用</span><span>状态</span><span>管理</span></div>{taskGroups.map((g,i)=><div className="admin-row task-group-row" key={i}><Link className="admin-group-link" to={`/admin/tasks/groups/${g.owner_id}/${g.account_id||'unbound'}`}><b>{g.username||'—'} · {g.remark||'账号'}<small>{g.nickname||''}</small></b></Link><span>{g.total||0}</span><span>{g.enabled||0}</span><Badge tone={g.validation_state==='valid'?'good':'warn'}>{g.validation_state||'未知'}</Badge><span className="row-actions"><Link className="button quiet" to={`/admin/tasks/groups/${g.owner_id}/${g.account_id||'unbound'}`}>打开任务</Link></span></div>)}</div><AdminPagination value={taskGroupsPagination} onChange={page=>setTaskGroupsPagination(p=>({...p,page}))}/></Card>}
    {section==='task-group'&&<Card><div className="section-head"><div><span className="eyebrow">TASK GROUP</span><h2>{groupDetail?.items?.[0]?.account||'账号任务'}</h2></div><Link to="/admin/tasks">返回分组 ↗</Link></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{setGroupTasksPage(1);setQuery(e.target.value)}} placeholder="搜索好友"/><span className="muted-copy">本页已选 {selectedTasks.size}</span></div>{selectedTasks.size>0&&<div className="admin-bulk-bar"><span>已选 {selectedTasks.size} 条任务</span><Button variant="quiet" onClick={()=>runBulk('/api/admin/tasks/bulk',{task_ids:[...selectedTasks],action:'resume'},'已恢复')}>批量恢复</Button><Button variant="danger" onClick={()=>runBulk('/api/admin/tasks/bulk',{task_ids:[...selectedTasks],action:'pause'},'已暂停')}>批量暂停</Button><Button variant="quiet" onClick={()=>setSelectedTasks(new Set())}>清除选择</Button></div>}{groupDetail?.items?.length?<div className="admin-table"><div className="table-head task-detail-head"><span>选择</span><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div>{groupDetail.items.map(item=><div className="admin-row task-detail-row" key={item.id}><input type="checkbox" checked={selectedTasks.has(item.id)} onChange={()=>toggleSelected(setSelectedTasks,item.id)} aria-label={`选择任务 ${item.name}`}/><b>{item.name}<small>{item.username} · {item.message_kind==='douyin_sticker'?'续火花表情':'文字消息'}</small></b><time>{item.time}</time><Badge tone={item.enabled?'good':'neutral'}>{item.enabled?'已启用':'已暂停'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>setSelectedTask(item)}>编辑</Button><Button variant="quiet" onClick={async()=>{try{await api(`/api/admin/tasks/${item.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,enabled:!item.enabled})});setFeedback(item.enabled?'任务已暂停':'任务已恢复');api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}&page=${groupTasksPage}`).then(setGroupDetail);onRefresh()}catch(e){setFeedback(e.message)}}}>{item.enabled?'暂停':'恢复'}</Button><Button variant="danger" onClick={async()=>{if(await confirmModal({title:'删除续火任务',eyebrow:'TASK WORKBENCH',message:`确定删除发给 ${item.name} 的任务吗？删除后不会影响其他好友任务。`}))api(`/api/admin/tasks/${item.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})}).then(()=>{setFeedback('任务已删除');api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}&page=${groupTasksPage}`).then(setGroupDetail);onRefresh()}).catch(e=>setFeedback(e.message))}}>删除</Button></span></div>)}</div>:<Empty title="暂无任务" text={groupDetail?'这个账号下还没有匹配的续火任务。':'正在读取任务明细…'}/>}<AdminPagination value={groupDetail?.pagination} onChange={page=>setGroupTasksPage(page)}/></Card>}
    {(section==='runs'||section==='today')&&<>
      {runDay==='today'&&<TodaySendBoard board={sendBoard}/>}
      <Card><AdminRunFilters query={runQuery} onQueryChange={value=>{setRunPage(1);setRunQuery(value)}} status={runStatusFilter} onStatusChange={value=>{setRunPage(1);setRunStatusFilter(value)}} day={runDay} onDayChange={value=>{setRunPage(1);setRunDay(value)}} start={runStartDate} end={runEndDate} onStartChange={setRunStartDate} onEndChange={setRunEndDate}/><AdminRunTable rows={runRows} onRetry={retryRun} onOpenUser={openUser}/><AdminPagination value={runPagination} onChange={setRunPage}/></Card>
    </>}
    {section==='operations'&&<><div className="metric-grid"><Metric label="CPU" value={liveOperations?.system?.cpu?.percent!=null?`${Math.round(liveOperations.system.cpu.percent)}%`:'—'} detail={liveOperations?.system?.scope_label||'服务器实时使用率'}/><Metric label="内存" value={liveOperations?.system?.memory?.percent!=null?`${Math.round(liveOperations.system.memory.percent)}%`:'—'} detail="服务器实时使用率" tone="accent"/><Metric label="磁盘" value={liveOperations?.system?.disk?.percent!=null?`${Math.round(liveOperations.system.disk.percent)}%`:'—'} detail="服务器实时使用率"/><Metric label="Worker" value={liveOperations?.services?.worker?.status==='healthy'?'运行中':liveOperations?.services?.worker?.label||'—'} detail="任务执行服务"/></div><div className="metric-grid admin-queue-metrics"><Metric label="待执行" value={liveOperations?.queue?.due_tasks??'—'} detail="当前到期任务"/><Metric label="运行中" value={liveOperations?.queue?.running_runs??'—'} detail="占用中的执行"/><Metric label="待重试" value={liveOperations?.queue?.retrying_runs??'—'} detail="延迟重试任务" tone="accent"/><Metric label="需关注" value={liveOperations?.queue?.attention_24h??'—'} detail="近 24 小时异常" tone="danger"/></div><Card><div className="section-head"><div><h2>服务器状态</h2><small className="muted">每 30 秒自动刷新</small></div><span className={`badge ${liveOperations?.services?.worker?.status==='healthy'?'good':'danger'}`}><i/>{liveOperations?.services?.worker?.status==='healthy'?'运行正常':'需要处理'}</span></div><p className="muted-copy">{liveOperations?.system?.scope_label||'当前服务器主机指标'} · 数据库 {liveOperations?.database?.size_bytes!=null?`${Math.round(liveOperations.database.size_bytes/1024/1024)} MB`:'—'} · 活跃登录 {liveOperations?.queue?.active_logins??'—'}</p></Card><UsageTrend history={operationsHistory}/></>}
    {section==='audit'&&<Card><div className="section-head"><h2>审计日志</h2><span className="eyebrow">{auditRows.length} EVENTS</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={auditQuery} onChange={e=>setAuditQuery(e.target.value)} placeholder="搜索操作、操作者、资源、请求 ID 或 IP"/></div><div className="admin-table"><div className="table-head audit-table-head"><span>操作</span><span>操作者</span><span>资源</span><span>时间</span></div>{auditRows.map(row=><button type="button" className="admin-row audit-row" key={row.id} onClick={()=>setSelectedAudit(row)}><b>{row.event}<small>{row.outcome==='failure'?'失败':row.outcome==='success'?'成功':'历史记录'}{row.error_code?` · ${row.error_code}`:''}</small></b><span>{row.actor||'系统'}</span><span>{row.subject_type||'—'}<small>{row.subject_id||''}</small></span><time>{row.created_at?new Date(row.created_at).toLocaleString('zh-CN'):'—'}</time></button>)}</div></Card>}
    {section==='diagnostics'&&<><Card><div className="section-head"><div><span className="eyebrow">RELIABILITY</span><h2>执行诊断</h2></div><span className="muted">最近执行</span></div><p className="muted-copy">最近 30 次执行的排队、提交和确认耗时。这里仅用于定位问题，不会影响任务调度。</p></Card><Card className="runs-card"><div className="run-list">{diagnostics?.items?.length?diagnostics.items.map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='success'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.name}</b><small>{item.stage||'—'} · 尝试 {item.attempt||1}</small></div><Badge tone={item.status==='success'?'good':item.status==='failed'?'danger':'warn'}>{item.status}</Badge><span className="run-error">{item.error_summary||`总耗时 ${item.total??'—'} 秒`}</span></div>):<Empty title="暂无诊断记录"/>}</div></Card></>}
    {section==='settings'&&!isMail&&<UpdatePanel update={update} data={{...data,mail:mailConfig}} onFeedback={setFeedback}/>}
    {isMail&&<MailAdmin data={{...data,mail:mailConfig}} onSaved={()=>{setFeedback('邮件配置已保存');setMailConfig(current=>({...current,enabled:true}));onRefresh()}}/>}
    <AdminTaskEditor task={selectedTask} csrf={data.csrf} onClose={()=>setSelectedTask(null)} onSaved={()=>{setSelectedTask(null);setFeedback('任务已保存');onRefresh()}}/>
    <AdminUserWorkspace workspace={userWorkspace} plans={data.plans||[]} csrf={data.csrf} actorId={data.user?.id} onClose={()=>setUserWorkspace(null)} onRefresh={refreshWorkspace} onFeedback={setFeedback} onTaskEdit={task=>setSelectedTask({...task,username:userWorkspace?.user?.username,account:userWorkspace?.accounts?.find(a=>a.id===task.accountId)?.name,account_id:task.accountId,target_sec_uid:task.sec_uid,message_kind:task.kind==='续火花表情'?'douyin_sticker':'text'})}/>
    <AuditDetailDrawer event={selectedAudit} onClose={()=>setSelectedAudit(null)}/>
  </>;
}
function UpdatePanel({update,data,onFeedback}) {
  const [busy,setBusy]=useState(false);
  const [rollbackVersion,setRollbackVersion]=useState('');
  const busyPhase=['checking','pulling','web_pulling','building','stopping','installing','web_installing','rollback','rollback_pulling'];
  const operationBusy=busy||busyPhase.includes(update?.phase);
  const submit=async action=>{if(operationBusy)return;setBusy(true);try{const version=action==='rollback'?(rollbackVersion||''):(action==='check'?'':(update?.available||''));if(action==='install'||action==='rollback'){if(!await confirmModal({title:action==='rollback'?'确认回滚版本':'确认完整更新',eyebrow:'RELEASE CHANNEL',message:action==='rollback'?`将回滚到 ${version||'最近安全版本'}，网页可能短暂不可用，继续吗？`:`将更新到 ${version}，网页可能短暂不可用，继续吗？`,confirmLabel:action==='rollback'?'确认回滚':'开始更新'})){setBusy(false);return;}}const send=()=>api(`/api/admin/updates/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrf},body:JSON.stringify({version})});let result;result=await send();onFeedback(result?.operation_id?`更新操作已排队（${result.operation_id.slice(0,8)}），状态会自动刷新。`:'更新检查已排队，状态会自动刷新。');}catch(error){onFeedback(error.message)}finally{setBusy(false)}};
  const rollbackOptions=update?.rollback_versions||[];
  return <><Card className="update-panel"><div className="section-head"><div><span className="eyebrow">RELEASE CHANNEL</span><h2>版本更新</h2></div><Badge tone={update?.online?'good':'neutral'}>{update?.online?'更新服务在线':'更新服务未连接'}</Badge></div><div className="update-version-grid"><div><small>当前版本</small><strong>{update?.current||'—'}</strong></div><div><small>最新版本</small><strong className={update?.update_available?'update-new':''}>{update?.latest||update?.current||'—'}</strong></div><div><small>运行方式</small><strong>{update?.web_current&&update.web_current!==update.current?'Web 独立版本':'完整镜像'}</strong></div></div>{update?.release_body&&<details className="update-release-notes"><summary>{update.release_name||'查看本次 Release 说明'}</summary><pre>{update.release_body}</pre></details>}<p className="muted-copy">更新会重新拉取固定镜像并校验摘要；Web-only 更新不会停止登录、发送和监控服务。</p><div className="setting-actions"><Button variant="quiet" disabled={operationBusy||!update?.online} onClick={()=>submit('check')}>检查更新</Button><Button variant="quiet" disabled={operationBusy||!update?.update_available} onClick={()=>submit('install-web')}>仅更新 Web</Button><Button disabled={operationBusy||!update?.update_available} onClick={()=>submit('install')}>完整更新</Button></div>{rollbackOptions.length>0&&<div className="update-rollback"><label>安全回滚版本<select className="input" value={rollbackVersion||rollbackOptions[0].version} onChange={e=>setRollbackVersion(e.target.value)}>{rollbackOptions.map(item=><option key={item.version} value={item.version}>{item.version}{item.published_at?` · ${new Date(item.published_at).toLocaleDateString('zh-CN')}`:''}</option>)}</select></label><Button variant="danger" disabled={operationBusy} onClick={()=>submit('rollback')}>回滚</Button></div>}{update?.phase&&<div className="update-progress"><span>{update.message||'正在处理更新…'}</span><b>{update.phase}</b></div>}</Card><AdminTotpPanel csrf={data.csrf}/><Card><div className="section-head"><div><span className="eyebrow">MAIL DELIVERY</span><h2>邮件服务</h2></div><Badge tone={data.mail?.enabled?'good':'neutral'}>{data.mail?.enabled?'已启用':'未启用'}</Badge></div><p className="muted-copy">发信配置保存在服务端密文中，密码不会回显。完整 SMTP 配置可在保存后测试连接。</p><div className="quick-links"><Link to="/notifications">打开用户通知设置 ↗</Link><Link to="/admin/settings/mail">打开完整邮件设置 ↗</Link></div></Card></>;
}
function AdminTotpPanel({csrf}) {
  const [state,setState]=useState(null);
  const [password,setPassword]=useState('');
  const [code,setCode]=useState('');
  const [pending,setPending]=useState(null);
  const [issuedCodes,setIssuedCodes]=useState([]);
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const refresh=async()=>{
    try { setState(await api('/api/auth/mfa/totp')); }
    catch(error) { setNotice(error.message); }
  };
  useEffect(()=>{refresh()},[]);
  const setup=async()=>{
    setBusy(true);setNotice('正在生成验证器密钥…');
    try {
      const value=await api('/api/auth/mfa/totp/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,password})});
      setPending(value);setCode('');setPassword('');setNotice('密钥已生成；请先添加到验证器，再输入当前验证码完成确认。');
      await refresh();
    } catch(error) { setNotice(error.message); }
    finally { setBusy(false); }
  };
  const confirm=async()=>{
    setBusy(true);setNotice('正在确认验证码…');
    try {
      const value=await api('/api/auth/mfa/totp/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,code})});
      if(!value?.ok)throw new Error(value?.detail||'验证码无效');
      setPending(null);setCode('');setIssuedCodes(value.recovery_codes||[]);setNotice('验证器已启用。请立即保存这组恢复码；它们只会显示这一次。');await refresh();
    } catch(error) { setNotice(error.message); }
    finally { setBusy(false); }
  };
  const regenerate=async()=>{
    setBusy(true);setNotice('正在轮换恢复码…');
    try {
      const value=await api('/api/auth/mfa/totp/recovery-codes/regenerate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,password,code})});
      if(!value?.ok)throw new Error(value?.detail||'恢复码轮换失败');
      setIssuedCodes(value.recovery_codes||[]);setPassword('');setCode('');setNotice('恢复码已轮换。旧恢复码已全部失效，请立即保存新的一组。');await refresh();
    } catch(error) { setNotice(error.message); }
    finally { setBusy(false); }
  };
  const copyRecoveryCode=async value=>{
    try { await navigator.clipboard.writeText(value); setNotice('恢复码已复制，请保存在密码管理器中。'); }
    catch { setNotice('复制失败，请手动选择恢复码。'); }
  };
  const disable=async()=>{
    if(!await confirmModal({title:state?.enabled?'停用管理员验证器':'取消验证器设置',eyebrow:'ADMIN SECURITY',message:state?.enabled?'停用后管理员验证器将不再保护高风险操作。':'尚未完成的验证器设置将被取消。'}))return;
    setBusy(true);setNotice('正在停用验证器…');
    try {
      const value=await api('/api/auth/mfa/totp/disable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,password,code})});
      if(!value?.ok)throw new Error(value?.detail||'停用失败');
      setPending(null);setPassword('');setCode('');setNotice('验证器已停用。');await refresh();
    } catch(error) { setNotice(error.message); }
    finally { setBusy(false); }
  };
  return <Card className="admin-totp-panel"><div className="section-head"><div><span className="eyebrow">ADMIN SECURITY</span><h2>管理员验证器</h2></div><Badge tone={state?.enabled?'good':state?.pending?'warn':'neutral'}>{state?.enabled?'已配置':state?.pending?'待确认':state?'未配置':'读取中'}</Badge></div><p className="muted-copy">可在 Google Authenticator、Microsoft Authenticator 或兼容应用中添加 TOTP。当前只保存配置，不会要求登录验证码。</p>{!state?.configured&&!pending&&<div className="form-grid"><label>当前管理员密码<input className="input" type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)}/></label><Button variant="quiet" disabled={busy||!password} onClick={setup}>{busy?'请稍候…':'开始设置验证器'}</Button></div>}{pending&&<div className="form-stack"><label>一次性设置密钥<input className="input" readOnly value={pending.secret} onFocus={event=>event.target.select()} aria-label="一次性 TOTP 设置密钥"/></label><small className="muted-copy">请在离开页面前将密钥手动添加到验证器；此密钥之后不会再次显示。</small><label>验证器当前 6 位验证码<input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={event=>setCode(event.target.value)}/></label><div className="setting-actions"><Button disabled={busy||code.trim().length!==6} onClick={confirm}>{busy?'正在确认…':'确认并启用'}</Button><Button variant="quiet" disabled={busy} onClick={disable}>取消设置</Button></div></div>}{state?.enabled&&<div className="form-stack"><div className="mfa-recovery-status"><b>恢复码</b><span>剩余 {state.recovery_codes_remaining??0} / 10 个。每个恢复码只能使用一次。</span></div><label>TOTP 验证码或恢复码<input className="input" inputMode="text" autoComplete="one-time-code" maxLength={19} placeholder="6 位验证码，或 XXXX-XXXX-XXXX-XXXX" value={code} onChange={event=>setCode(event.target.value)}/></label><label>当前管理员密码<input className="input" type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)}/></label><div className="setting-actions"><Button variant="quiet" disabled={busy||code.trim().length!==6||!password} onClick={regenerate}>轮换恢复码</Button><Button variant="danger" disabled={busy||code.trim().length<6||!password} onClick={disable}>验证后停用</Button></div></div>}{issuedCodes.length>0&&<div className="mfa-recovery-issued"><b>请现在保存恢复码</b><small>关闭或刷新页面后不会再次显示。轮换会立即使上一组恢复码失效。</small>{issuedCodes.map(value=><div key={value}><code>{value}</code><Button variant="quiet" onClick={()=>copyRecoveryCode(value)}>复制</Button></div>)}</div>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}</Card>;
}
function MailAdmin({data,onSaved}) {
  const config=data.mail||{};
  const [form,setForm]=useState({host:config.host||'',port:config.port||465,mode:config.mode||'ssl',username:config.username||'',password:'',sender:config.sender||'',sender_name:config.sender_name||'火花守护',enabled:!!config.enabled,events:config.events||[]});
  const [status,setStatus]=useState('');
  const change=(key,value)=>setForm(current=>({...current,[key]:value}));
  const send=async action=>{setStatus('正在处理…');try{const value=await api(`/admin/settings/mail/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form,csrf_token:data.csrf})});setStatus(value.message||'操作完成');if(action==='save')onSaved()}catch(e){setStatus(e.message)}};
  return <div className="mail-settings-grid"><Card><div className="section-head"><div><span className="eyebrow">SMTP CONFIGURATION</span><h2>发信服务</h2></div><Badge tone={form.enabled?'good':'neutral'}>{form.enabled?'已启用':'未启用'}</Badge></div><div className="form-stack"><label>SMTP 主机<input className="input" value={form.host} onChange={e=>change('host',e.target.value)} placeholder="smtp.example.com"/></label><div className="form-grid"><label>端口<input className="input" type="number" value={form.port} onChange={e=>change('port',e.target.value)}/></label><label>加密方式<select className="input" value={form.mode} onChange={e=>change('mode',e.target.value)}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option></select></label></div><label>SMTP 用户名<input className="input" value={form.username} onChange={e=>change('username',e.target.value)}/></label><label>SMTP 授权码<input className="input" type="password" value={form.password} onChange={e=>change('password',e.target.value)} placeholder={config.password_configured?'已配置，留空保持不变':'填写授权码'}/></label><div className="form-grid"><label>发件邮箱<input className="input" value={form.sender} onChange={e=>change('sender',e.target.value)}/></label><label>发件人名称<input className="input" value={form.sender_name} onChange={e=>change('sender_name',e.target.value)}/></label></div><label className="check-line"><input type="checkbox" checked={form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用邮件通知服务</label>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button onClick={()=>send('save')}>保存设置</Button><Button variant="quiet" onClick={()=>send('test')}>测试连接</Button></div></div></Card><Card><span className="eyebrow">DELIVERY LOG</span><h2>发送记录</h2><div className="run-list mail-deliveries">{(config.deliveries||[]).map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='accepted'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.event}</b><small>{item.recipient} · {item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):''}</small></div><Badge tone={item.status==='accepted'?'good':item.status==='failed'?'danger':'warn'}>{item.status}</Badge></div>)}</div></Card></div>;
}
  function UsageTrend({history}) {
    const points=Array.isArray(history?.points)?history.points:[];
    const series=[{key:'cpu',label:'CPU',color:'#ff6b25'},{key:'memory',label:'内存',color:'#6ee7b7'},{key:'disk',label:'磁盘',color:'#fbbf24'}];
    const valuesFor=key=>points.map(point=>Number(point?.[key])).filter(Number.isFinite);
    const average=values=>values.length?Math.round(values.reduce((sum,value)=>sum+value,0)/values.length):null;
    const latest=key=>{for(let index=points.length-1;index>=0;index-=1){const value=Number(points[index]?.[key]);if(Number.isFinite(value))return value;}return null;};
    const peak=key=>{const values=valuesFor(key);return values.length?Math.max(...values):null;};
    const tone=value=>value==null?'neutral':value>=80?'danger':value>=60?'warn':'good';
    return <Card className="usage-trend-card"><div className="section-head"><div><span className="eyebrow">RESOURCE HISTORY</span><h2>服务器资源</h2></div><span className="muted-copy">近 24 小时 · {history?.state==='fresh'?'实时采样':'等待采样'}</span></div>{points.length?<div className="usage-table" role="table" aria-label="服务器资源使用率"><div className="usage-table-head" role="row"><span>指标</span><span>当前</span><span>24 小时峰值</span><span>平均</span><span>趋势</span><span>状态</span></div>{series.map(item=>{const current=latest(item.key),max=peak(item.key),avg=average(valuesFor(item.key));return <div className="usage-table-row" role="row" key={item.key}><strong><i style={{background:item.color}}/>{item.label}</strong><b>{current==null?'—':`${Math.round(current)}%`}</b><span>{max==null?'—':`${Math.round(max)}%`}</span><span>{avg==null?'—':`${avg}%`}</span><div className="usage-meter"><i style={{width:`${Math.min(100,Math.max(0,current||0))}%`,background:item.color}}/></div><Badge tone={tone(current)}>{current==null?'无数据':current>=80?'高负载':current>=60?'需关注':'正常'}</Badge></div>})}</div>:<div className="usage-empty">监控服务启动后会在这里显示真实采样数据。</div>}<div className="usage-table-foot"><span>采样范围：{history?.start?new Date(history.start*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'24 小时前'} - {history?.latest_at?new Date(history.latest_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'—'}</span><span>数据源：服务器监控服务</span></div></Card>;
  }

  function AdminRuns({rows=[]}) { return <div className="run-list admin-runs">{rows.length?rows.map(r=><div className="run-row" key={r.id}><span className={`run-dot ${r.tone||''}`}/><div className="grow"><b>{r.target}</b><small>{r.owner} · {r.time}</small></div><Badge tone={r.tone||'neutral'}>{r.status}</Badge></div>):<Empty title="暂无记录"/>}</div>; }
function AdminPagination({value,onChange}) { if(!value||value.pages<=1)return null; return <div className="admin-pagination"><span>共 {value.total} 条 · 第 {value.page} / {value.pages} 页</span><div><Button variant="quiet" disabled={!value.has_previous} onClick={()=>onChange(value.page-1)}>上一页</Button><Button variant="quiet" disabled={!value.has_next} onClick={()=>onChange(value.page+1)}>下一页</Button></div></div>; }
function AdminRunTable({rows,onRetry,onOpenUser}) {
  return <div className="admin-runs-ledger">{rows.length?rows.map(row=><article className="admin-run-record" key={row.id}><span className={`run-dot ${row.tone||'neutral'}`}/><div className="admin-run-main"><div className="admin-run-title"><b>{row.target}</b><Badge tone={row.tone||'neutral'}>{row.label||row.status}</Badge></div><div className="admin-run-meta"><button type="button" onClick={()=>onOpenUser?.({id:row.owner_id,username:row.owner})}>{row.owner}</button><span>{row.account}</span><time>{row.scheduled_for}</time><span>{row.stage}</span></div>{row.error&&<p className={row.status==='failed'?'run-error danger-text':'run-error'}>{row.error}</p>}{row.guidance?.text&&<p className="run-guidance-copy">{row.guidance.text}</p>}{row.attempts?.length>0&&<details className="admin-run-attempts"><summary>{row.attempts.length} 次尝试详情</summary>{row.attempts.map((attempt,index)=><div key={`${row.id}-${index}`}><span>第 {attempt.number||index+1} 次</span><span>{attempt.stage||'—'}</span><span>{attempt.reason||attempt.status||'—'}</span></div>)}</details>}</div>{row.can_retry&&<Button variant="quiet" onClick={()=>onRetry(row.id)}>补发</Button>}</article>):<Empty title="没有匹配的执行记录" text="调整日期或状态筛选后再查看。"/>}</div>;
}
function TodaySendBoard({board}) {
  const totals=board?.totals||{}; const groups=asList(board?.groups);
  return <><div className="metric-grid today-send-metrics"><Metric label="今日计划" value={totals.planned||0} detail="当前启用任务"/><Metric label="已发出" value={totals.success||0} detail="执行已确认" tone="good"/><Metric label="未发出" value={totals.missed||0} detail="不代表账号失效" tone="accent"/><Metric label="待核实 / 验证" value={(totals.unconfirmed||0)+(totals.login||0)} detail={`${totals.unconfirmed||0} 待核实 · ${totals.login||0} 需验证`} tone="danger"/></div><p className="today-board-date">北京时间 {board?.day||'—'} · 任务明细与补发操作统一显示在下方执行流水</p><div className="today-board-groups">{groups.length?groups.map(group=><div className="today-board-account" key={group.key}><div><b>{group.account_name}</b><small>{group.username} · 计划 {group.counts?.planned||0}</small></div><span><Badge tone={group.health?.tone||'neutral'}>{group.health?.label||'账号状态未知'}</Badge><small>发出 {group.counts?.success||0} · 未发出 {group.counts?.missed||0} · 待核实 {group.counts?.unconfirmed||0}</small></span></div>):<Empty title="今日没有启用中的续火计划" text="可在用户管理的任务视图查看所有任务。"/>}</div></>;
}
function AdminRunFilters({query,onQueryChange,status,onStatusChange,day,onDayChange,start,end,onStartChange,onEndChange}) {
  return <div className="toolbar admin-run-toolbar"><input className="input compact-search" value={query} onChange={e=>onQueryChange(e.target.value)} placeholder="搜索客户、目标或错误"/><select className="input compact-search" value={status} onChange={e=>onStatusChange(e.target.value)}><option value="">全部结果</option><option value="success">已发出</option><option value="failed">未发出</option><option value="submitted_unconfirmed">待核实</option><option value="running">执行中</option><option value="skipped">已跳过</option></select>{day==='range'&&<><input className="input compact-search" type="date" value={start} onChange={e=>onStartChange(e.target.value)} aria-label="开始日期"/><input className="input compact-search" type="date" value={end} onChange={e=>onEndChange(e.target.value)} aria-label="结束日期"/></>}<select className="input compact-search" value={day} onChange={e=>onDayChange(e.target.value)}><option value="today">今日</option><option value="all">全部时间</option><option value="range">日期范围</option></select></div>;
}
function AdminInsights({insights,days,onDaysChange,onOpenUser}) {
  const trend=asList(insights?.trend); const max=Math.max(1,...trend.map(item=>item.total||0)); const alerts=insights?.alerts||{};
  return <><div className="admin-insights-grid"><Card className="admin-trend-card"><div className="section-head"><div><span className="eyebrow">DELIVERY HEALTH</span><h2>发送趋势</h2></div><select className="input compact-search" value={days} onChange={e=>onDaysChange(Number(e.target.value))}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></div><div className="admin-trend-bars">{trend.map(item=><div className="admin-trend-day" key={item.date} title={`${item.date} · ${item.success}/${item.total} 成功，${item.failed||0} 失败或待核实`}><div className="admin-trend-track"><i style={{height:`${Math.round(((item.success||0)/max)*100)}%`}}/><b style={{height:`${Math.round(((item.failed||0)/max)*100)}%`}}/></div><small>{item.date.slice(5)}</small></div>)}</div><div className="admin-trend-legend"><span><i className="trend-success"/>已发出</span><span><i className="trend-failed"/>失败/待核实</span><small>按北京时间统计</small></div></Card><Card className="admin-alert-card"><div className="section-head"><div><span className="eyebrow">NEEDS ATTENTION</span><h2>需要处理</h2></div><Badge tone={(alerts.accounts?.length||alerts.failed_runs?.length||alerts.expiring?.length)?'warn':'good'}>{(alerts.accounts?.length||0)+(alerts.failed_runs?.length||0)+(alerts.expiring?.length||0)} 项</Badge></div><div className="admin-alert-list">{(alerts.accounts||[]).slice(0,3).map(item=><button className="admin-alert-row" key={`account-${item.id}`} onClick={()=>onOpenUser?.({id:item.owner_id})}><span className="alert-marker danger"/><span><b>{item.owner} · {item.name}</b><small>抖音账号{item.label}，点击查看客户工作台</small></span><span>›</span></button>)}{(alerts.failed_runs||[]).slice(0,3).map(item=><div className="admin-alert-row" key={`run-${item.id}`}><span className="alert-marker danger"/><span><b>{item.username} · {item.name}</b><small>{item.time} · {item.error||'执行失败或待核实'}</small></span><Link to={`/admin/runs`}>查看</Link></div>)}{(alerts.expiring||[]).slice(0,3).map(item=><button className="admin-alert-row" key={`subscription-${item.id}`} onClick={()=>onOpenUser?.({id:item.id})}><span className="alert-marker warn"/><span><b>{item.username} · 订阅即将到期</b><small>{item.days_remaining} 天后到期</small></span><span>›</span></button>)}{!alerts.accounts?.length&&!alerts.failed_runs?.length&&!alerts.expiring?.length&&<div className="admin-alert-clear"><span className="live-dot"/><span>当前没有需要处理的异常</span></div>}</div></Card></div></>;
}
function AuditDetailDrawer({event,onClose}) {
  const [detail,setDetail]=useState(event||null); const [error,setError]=useState('');
  useEffect(()=>{setDetail(event||null);setError('');if(event?.id)api(`/api/admin/audit/${event.id}`).then(setDetail).catch(e=>setError(e.message))},[event?.id]);
  return <Drawer open={!!event} onClose={onClose} title="审计详情" eyebrow="AUDIT EVENT" wide><div className="drawer-content">{detail&&<dl className="audit-detail-list"><dt>操作</dt><dd>{detail.event}</dd><dt>结果</dt><dd>{detail.outcome==='failure'?'失败':detail.outcome==='success'?'成功':'历史记录'}{detail.error_code?` · ${detail.error_code}`:''}</dd><dt>操作者</dt><dd>{detail.actor||'—'}</dd><dt>资源</dt><dd>{detail.subject_type||'—'} · {detail.subject_id||'—'}</dd><dt>请求 ID</dt><dd>{detail.request_id||'—'}</dd><dt>客户端 IP</dt><dd>{detail.client_ip||'—'}</dd><dt>时间</dt><dd>{detail.created_at?new Date(detail.created_at).toLocaleString('zh-CN'):'—'}</dd><dt>详情</dt><dd>{detail.detail||'没有额外说明'}</dd></dl>}{error&&<div className="inline-feedback danger-text">{error}</div>}</div></Drawer>;
}
function AdminTaskEditor({task, csrf, onClose, onSaved}) {
  const [form,setForm]=useState({time:task?.time||'09:30', kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text', message:task?.message||''});
  const [status,setStatus]=useState('');
  useEffect(()=>setForm({time:task?.time||'09:30',kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text',message:task?.message||''}),[task]);
  if(!task)return null;
  const save=async e=>{e.preventDefault();setStatus('正在保存…');try{await api(`/api/admin/tasks/${task.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,account_id:task.account_id,target_name:task.name,target_sec_uid:task.target_sec_uid||'',send_time:form.time,message_kind:form.kind,message_template:form.kind==='text'?form.message:'续火花'})});setStatus('已保存');onSaved()}catch(error){setStatus(error.message)}};
  return <Drawer open={!!task} onClose={onClose} title={`编辑 ${task.name}`} eyebrow="TASK WORKBENCH" wide><div className="drawer-content"><form className="form-stack admin-editor-form" onSubmit={save}><div className="edit-target"><span className="avatar avatar-large">{task.name?.slice(0,1)}</span><div><b>{task.name}</b><small>{task.username} · {task.account||'未绑定账号'}</small></div><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'已启用':'已暂停'}</Badge></div><div className="admin-form-section"><span className="eyebrow">DELIVERY PLAN</span><label>每日发送时间<input className="input" type="time" value={form.time} onChange={e=>setForm(v=>({...v,time:e.target.value}))}/></label><label>消息类型<select className="input" value={form.kind} onChange={e=>setForm(v=>({...v,kind:e.target.value}))}><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></select></label>{form.kind==='text'&&<label>消息内容<textarea className="input textarea" value={form.message} onChange={e=>setForm(v=>({...v,message:e.target.value}))}/></label>}</div><div className="admin-locked-target"><span>目标好友</span><b>{task.name}</b><small>稳定身份已锁定，编辑不会重新选择好友</small></div>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button type="submit">保存任务</Button><Button variant="quiet" type="button" onClick={onClose}>取消</Button></div></form></div></Drawer>;
}
function AdminUserWorkspace({workspace, plans=[], csrf, actorId, onClose, onRefresh, onFeedback, onTaskEdit}) {
  const user=workspace?.user||{}; const membership=workspace?.membership||{};
  const accounts=Array.isArray(workspace?.accounts)?workspace.accounts:[];
  const tasks=Array.isArray(workspace?.tasks)?workspace.tasks:[];
  const runs=Array.isArray(workspace?.runs)?workspace.runs:[];
  const validAccounts=accounts.filter(account=>account.validation_state==='valid'||account.health?.state==='valid').length;
  const successfulRuns=runs.filter(run=>run.status==='success').length;
  const failedRuns=runs.filter(run=>['failed','error','submitted_unconfirmed'].includes(run.status)).length;
  const runSuccessRate=runs.length?Math.round(successfulRuns/runs.length*100):0;
  const overLimit=membershipOverLimit(membership);
  const [limits,setLimits]=useState({accounts:membership.max_accounts||1,tasks:membership.max_tasks||10});
  const [planId,setPlanId]=useState(plans[0]?.id||'');
  useEffect(()=>setLimits({accounts:membership.max_accounts||1,tasks:membership.max_tasks||10}),[user.id,membership.max_accounts,membership.max_tasks]);
  useEffect(()=>{if(plans.length&&!plans.some(plan=>plan.id===planId))setPlanId(plans[0].id)},[plans,planId]);
  if(!workspace)return null;
  const mutate=async(path,options={})=>{try{const value=await api(path,options);onFeedback(value?.message||'操作已完成');onRefresh()}catch(error){onFeedback(error.message)}};
  const reset=async()=>{try{const value=await api(`/api/admin/users/${user.id}/reset-password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})});onFeedback(value?.temporary_password?`临时密码：${value.temporary_password}`:'密码已重置');onRefresh()}catch(error){onFeedback(error.message)}};
  const toggle=()=>mutate(`/api/admin/users/${user.id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})});
  const changeRole=async()=>{
    const nextRole=user.role==='admin'?'user':'admin';
    const label=nextRole==='admin'?'升级为管理员':'降为普通用户';
    if(!await confirmModal({title:label,eyebrow:'CUSTOMER WORKBENCH',message:`确定将 ${user.username} ${label}吗？`}))return;
    mutate(`/api/admin/users/${user.id}/role`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,role:nextRole})});
  };
  const extend=()=>mutate(`/admin/users/${user.id}/subscription`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrf_token:csrf,duration_days:'30',plan_id:planId,max_accounts:String(membership.max_accounts||1),max_tasks:String(membership.max_tasks||10)})});
  const saveLimits=async()=>{try{const result=await api(`/api/admin/subscriptions/${user.id}/limits`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,max_accounts:Number(limits.accounts),max_tasks:Number(limits.tasks)})});onFeedback(`额度已更新：${result.max_accounts} 个账号 / ${result.max_tasks} 个任务`);onRefresh()}catch(error){onFeedback(error.message)}};
  const taskAction=async task=>{const enabled=!task.enabled;try{await api(`/api/admin/tasks/${task.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,enabled})});onFeedback(enabled?'任务已恢复':'任务已暂停');onRefresh()}catch(error){onFeedback(error.message)}};
  return <Drawer open={!!workspace} onClose={onClose} title={adminUserLabel(user)} eyebrow="CUSTOMER WORKBENCH" wide><div className="drawer-content"><AdminNotePanel workspace={workspace} csrf={csrf} onFeedback={onFeedback} onRefresh={onRefresh}/><div className="workspace-summary"><div><h3>{adminUserLabel(user)}</h3><p>{user.role==='admin'?'管理员':'普通用户'} · 注册于 {user.created}</p></div><Badge tone={user.status==='active'?'good':'danger'}>{user.status==='active'?'正常':'已停用'}</Badge></div><div className="metric-grid workspace-metrics"><Metric label="抖音账号" value={workspace.accounts?.length||0} detail="已绑定"/><Metric label="续火任务" value={workspace.tasks?.length||0} detail={overLimit>0?`超出 ${overLimit} 个`:'全部任务'} tone={overLimit>0?'danger':'accent'}/><Metric label="执行记录" value={workspace.runs?.length||0} detail="最近记录"/><Metric label="订阅" value={membership.active?'有效':'未开通'} detail={membership.days_remaining!=null?`剩余 ${membership.days_remaining} 天`:'管理员'} /></div><section className="workspace-section-card"><div className="section-head"><h3>账号与状态</h3><span className="eyebrow">{workspace.accounts?.length||0} ACCOUNTS</span></div>{workspace.accounts?.length?workspace.accounts.map(account=><div className="admin-row compact" key={account.id}><b>{account.name}<small>{account.profile?.nickname||'抖音账号'}</small></b><Badge tone={account.validation_state==='valid'?'good':'warn'}>{account.health?.label||account.validation_state||'未知'}</Badge></div>):<Empty title="未绑定抖音账号"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>任务与好友</h3><span className="eyebrow">{workspace.tasks?.length||0} TASKS</span></div>{workspace.tasks?.length?workspace.tasks.map(task=><div className="admin-row compact" key={task.id}><b>{task.name}<small>{task.time} · {task.kind}</small></b><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'运行中':'已暂停'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>onTaskEdit?.(task)}>编辑</Button><Button variant="quiet" onClick={()=>taskAction(task)}>{task.enabled?'暂停':'恢复'}</Button></span></div>):<Empty title="暂无续火任务"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>最近执行</h3><span className="eyebrow">{workspace.runs?.length||0} RUNS</span></div>{workspace.runs?.length?workspace.runs.slice(0,6).map(run=><div className="admin-row compact" key={run.id}><b>{run.name}<small>{run.time} · {run.error||'执行完成'}</small></b><Badge tone={run.status==='success'?'good':run.status==='failed'?'danger':'warn'}>{run.label||run.status}</Badge></div>):<Empty title="暂无执行记录"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>订阅与额度</h3><span className="eyebrow">SUBSCRIPTION</span></div><div className="workspace-subscription-line"><b>{membership.status_label||'未开通'}</b><span>{membership.valid_until?new Date(membership.valid_until).toLocaleDateString('zh-CN'):'—'} · 剩余 {membership.days_remaining??'—'} 天</span><select className="input compact-search workspace-plan-select" value={planId} onChange={e=>setPlanId(e.target.value)}>{plans.map(plan=><option value={plan.id} key={plan.id}>{plan.name} · ¥{plan.price_yuan}</option>)}</select><Button variant="quiet" onClick={extend}>按套餐续期 30 天</Button></div><MembershipPlanNotice membership={membership}/>{user.role!=='admin'&&<div className="workspace-limit-controls"><label>账号额度<input className="input" type="number" min="1" max="100" value={limits.accounts} onChange={e=>setLimits(v=>({...v,accounts:e.target.value}))}/></label><label>任务额度<input className="input" type="number" min="1" max="1000" value={limits.tasks} onChange={e=>setLimits(v=>({...v,tasks:e.target.value}))}/></label><Button variant="quiet" onClick={saveLimits}>保存额度</Button><Button variant="danger" disabled={!membership.active} onClick={async()=>{if(await confirmModal({title:'撤销订阅',eyebrow:'SUBSCRIPTION WORKBENCH',message:`确定撤销 ${user.username} 的订阅吗？撤销后用户将无法创建新任务。`}))mutate(`/api/admin/subscriptions/${user.id}/revoke`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})})}}>撤销订阅</Button></div>}</section><div className="setting-actions workspace-actions">{user.id!==actorId&&<Button variant="quiet" onClick={changeRole}>{user.role==='admin'?'降为普通用户':'升级为管理员'}</Button>}<Button variant="quiet" onClick={toggle}>{user.status==='active'?'停用用户':'启用用户'}</Button><Button variant="quiet" onClick={reset}>重置密码</Button><Button variant="quiet" onClick={onClose}>关闭</Button></div></div></Drawer>;
}
function AdminNotePanel({workspace, csrf, onFeedback, onRefresh}) {
  const user=workspace?.user;
  const [note,setNote]=useState(user?.admin_note||'');
  useEffect(()=>setNote(user?.admin_note||''),[user?.id,user?.admin_note]);
  if(!user)return null;
  const save=async()=>{try{const value=await api(`/api/admin/users/${user.id}/note`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,admin_note:note})});setNote(value.admin_note||'');onFeedback('用户备注已保存');onRefresh()}catch(error){onFeedback(error.message)}};
  return <section className="workspace-section-card admin-note-panel"><div className="section-head"><div><h3>管理员备注</h3><p className="muted-copy">仅管理员可见，用于区分熟人和推广用户。</p></div><span className="eyebrow">PRIVATE NOTE</span></div><div className="admin-note-edit-row"><textarea className="input textarea" maxLength="500" rows="2" value={note} onChange={e=>setNote(e.target.value)} placeholder="例如：朋友介绍 / 熟人免费"/><Button variant="quiet" onClick={save}>保存备注</Button></div></section>;
}
function AuthPassword({name,id,label,autoComplete,required=true,minLength}) { const [shown,setShown]=useState(false); return <label className="auth-field"><span>{label}</span><span className="auth-password"><input id={id} name={name} type={shown?'text':'password'} autoComplete={autoComplete} minLength={minLength} maxLength={minLength?256:undefined} required={required}/><button type="button" className="auth-password-toggle" onClick={()=>setShown(v=>!v)} aria-label={shown?'隐藏密码':'显示密码'}>{shown?'隐藏':'显示'}</button></span>{minLength&&<small>至少 12 位，包含字母和数字；请勿使用常见、连续或重复模式</small>}</label>; }
function AuthPage({kind}) {
  const params=new URLSearchParams(window.location.search); const error=params.get('error'); const username=params.get('username')||''; const registered=params.get('registered')==='1'; const sent=params.get('sent')==='1';
  const [csrf,setCsrf]=useState('');
  useEffect(()=>{const onClick=event=>{const link=event.target.closest?.('.fire-auth-tabs a');if(!link)return;event.preventDefault();window.history.pushState({},'',link.getAttribute('href'));window.dispatchEvent(new PopStateEvent('popstate'));};document.addEventListener('click',onClick);return()=>document.removeEventListener('click',onClick)},[]);
  useEffect(()=>{if(kind==='change')api('/api/auth/bootstrap').then(v=>{const token=v?.csrf||'';setCsrf(token);const form=document.querySelector('.auth-form');if(form){let field=form.querySelector('[name="csrf_token"]');if(!field){field=document.createElement('input');field.type='hidden';field.name='csrf_token';form.prepend(field)}field.value=token}}).catch(()=>{})},[kind]);
  const title=kind==='login'?'欢迎回来':kind==='register'?'创建账号':kind==='forgot'?'找回网站密码':'设置登录密码';
  const action=kind==='login'?'/login':kind==='register'?'/register':kind==='forgot'?(sent?'/forgot-password/reset':'/forgot-password'):'/change-password';
  return <main className="fire-auth-shell"><div className="fire-auth-glow fire-auth-glow-a" aria-hidden="true"/><div className="fire-auth-glow fire-auth-glow-b" aria-hidden="true"/><header className="fire-auth-top"><Logo/><ServiceIndicator/></header><section className="fire-auth-card"><div className="fire-auth-intro"><span className="eyebrow">DYFIRE / ACCOUNT</span><h1>{title}</h1><p>{kind==='login'?'登录后管理抖音账号与续火任务。':kind==='register'?'注册后开通订阅，即可绑定抖音账号。':kind==='forgot'?'使用已验证邮箱找回网站密码。':'设置一个新的平台登录密码。'}</p></div><nav className="fire-auth-tabs" aria-label="账户入口"><a className={kind==='login'?'active':''} href="/login">登录</a><a className={kind==='register'?'active':''} href="/register">注册</a></nav>{registered&&<div className="auth-notice good" role="status">注册成功，请登录后兑换会员时长。</div>}{sent&&<div className="auth-notice" role="status">如果账号和已验证邮箱匹配，重置邮件会进入发送队列。</div>}{error&&<div className="auth-notice danger" role="alert">{error}</div>}<form className="auth-form fire-auth-form" method="post" action={action}>{kind==='login'&&<><label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" placeholder="输入用户名或邮箱" required autoFocus/></label><AuthPassword name="password" id="auth-login-password" label="密码" autoComplete="current-password"/></>}{kind==='register'&&<><label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" minLength="3" placeholder="设置用户名或邮箱" required autoFocus/><small>3–32 位字母、数字、@、点、下划线或短横线</small></label><AuthPassword name="password" id="auth-register-password" label="密码" autoComplete="new-password" minLength={12}/><AuthPassword name="password_confirmation" id="auth-register-password-confirmation" label="确认密码" autoComplete="new-password" minLength={12}/></>}{kind==='forgot'&&!sent&&<><label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} autoComplete="username" required/></label><label className="auth-field"><span>已验证的接收邮箱</span><input name="email" type="email" autoComplete="email" required/></label></>}{kind==='forgot'&&sent&&<><label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} required/></label><label className="auth-field"><span>重置代码</span><input name="code" inputMode="text" autoComplete="one-time-code" minLength="8" maxLength="12" required/><small>新代码为 12 位；更新前已发出的 8 位代码在有效期内仍可使用</small></label><AuthPassword name="password" id="auth-reset-password" label="新密码" autoComplete="new-password" minLength={12}/><AuthPassword name="confirmation" id="auth-reset-confirmation" label="再次输入新密码" autoComplete="new-password" minLength={12}/></>}{kind==='change'&&<><AuthPassword name="current_password" id="auth-current-password" label="当前密码" autoComplete="current-password"/><AuthPassword name="new_password" id="auth-new-password" label="新密码" autoComplete="new-password" minLength={12}/><AuthPassword name="new_password_confirmation" id="auth-new-password-confirmation" label="确认新密码" autoComplete="new-password" minLength={12}/></>}<button className="auth-submit" type="submit">{kind==='login'?'登录':kind==='register'?'注册并返回登录':kind==='forgot'?(sent?'重置密码':'发送重置邮件'):'保存新密码'} <span>↗</span></button></form><footer className="auth-footer fire-auth-footer">{kind==='login'?<><a href="/forgot-password">忘记网站密码？</a><span>还没有账号？<a href="/register">立即注册</a></span></>:<a href="/login">返回登录</a>}<small>请妥善保管平台密码和抖音登录信息。</small></footer></section><p className="fire-auth-caption">每天续上火花，保持重要的连接。</p></main>;
}
function App() {
  useEffect(() => { const overlay=installSelectOverlay(); return () => overlay?.destroy?.(); }, []);
  const [data,setData]=useState(null);
  const [error,setError]=useState('');
  const location=useLocation();
  const authKind=location.pathname==='/login'?'login':location.pathname==='/register'?'register':location.pathname==='/forgot-password'?'forgot':location.pathname==='/change-password'?'change':null;
  const refresh=React.useCallback(()=>{
    setError('');
    return api(location.pathname.startsWith('/admin')?'/api/admin/bootstrap':'/api/app/bootstrap')
      .then(value=>{setData(normalizeBootstrap(value));return normalizeBootstrap(value)})
      .catch(error=>{setError(error.message);return null});
  },[location.pathname]);
  useEffect(()=>{if(!authKind)refresh()},[refresh,authKind]);
  if(authKind)return <AuthPage kind={authKind}/>;
  if(error&&!data)return <div className="loading-screen"><div className="loading-mark">✦</div><ErrorState message={error} onRetry={()=>refresh().catch(()=>{})}/><a href="/login">返回登录</a></div>;
  if(!data)return <div className="loading-screen"><div className="loading-mark pulse">✦</div><LoadingState/></div>;
  const path=location.pathname;
  let page;
  if(path.startsWith('/admin'))page=<Admin data={data} onRefresh={refresh}/>;
  else if(path==='/dashboard'||path==='/')page=<Dashboard data={data} onRefresh={refresh}/>;
  else if(path==='/studio'||path==='/tasks')page=<Studio data={data} onRefresh={refresh}/>;
  else if(path==='/accounts')page=<Accounts data={data} onRefresh={refresh}/>;
  else if(path==='/runs')page=<Runs data={data}/>;
  else if(path.startsWith('/tasks/')&&path.endsWith('/edit'))page=<TaskEdit data={data} id={path.split('/')[2]} onRefresh={refresh}/>;
  else if(path==='/notifications')page=<Notifications data={data}/>;
  else if(path==='/subscription'||path==='/account-center'||path==='/attention'||path==='/help')page=<SimplePage data={data} type={path.slice(1)}/>;
  else page=<Dashboard data={data}/>;
  return <AppShell data={data}>{page}</AppShell>;
}

createRoot(document.getElementById('app')).render(<BrowserRouter><RouteErrorBoundary><App/></RouteErrorBoundary></BrowserRouter>);
