import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import {createApiClient} from './api-client.js';
import {FilterSummary,ListSkeleton,ReadStatus,RowFeedback,useRowFeedback} from './ui-continuity.jsx';
import {accountNeedsRecovery,accountCheckResult} from './account-check-state.js';
import AccountLifecycle from './account-lifecycle.jsx';
import React, {useEffect, useId, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {BrowserRouter, Link, useLocation, useNavigate} from 'react-router-dom';
import {focusTrapTarget, getMobileDockRoutes} from './navigation.js';
import {contactKey, createContactPickerController} from './contact-picker-state.js';
import {createTaskComposerController, mergeTaskRows} from './task-composer-state.js';
import {MotionDisclosure, MotionList} from './workflow-motion.jsx';
import {AnimatePresence, motion, useReducedMotion} from 'motion/react';
import DailyOverview from './daily-overview.jsx';
import RowActions from './row-actions.jsx';
import {useAccountNavigation} from './context-navigation.jsx';
import {AnimatedDetails, TabTransitionPanel, useViewportReveal} from './ui-motion.jsx';
import {Toast, UserNavigation, WorkspaceSkeleton, ActivityHeatmap, flashTask, RouteScroll} from './experience-components.jsx';
// Reveal content sections without remounting routes or losing filter drafts.
function RouteMotionDiv({children, locationKey}) {
  const ref=useRef(null);
  useViewportReveal(ref,locationKey);
  return <div ref={ref} className="route-view" data-page={locationKey}>{children}</div>;
}
import './react.css';
import './react-polish.css';
import './react-fire.css';
import RubberSegment from './components/RubberSegment';
import {Button, LiveNumber, SiteSelect, StatusIcon} from './ui-controls.jsx';
import {FeedbackHost} from './ui-feedback.jsx';
import {Drawer} from './ui-dialog.jsx';
import {ContactPanel} from './ui-panels.jsx';
import './ui-system.css';
import './ui-continuity.css';
import './ui-mobile.css';
import {installSelectOverlay} from './select-overlay.js';
import {hasConfirmedExecution} from './onboarding-state.js';
const AuthPage=React.lazy(()=>import('./auth-pages.jsx'));
const AccountIncidents=React.lazy(()=>import('./account-incidents.jsx'));
import {createBindingComponents} from './binding-components.jsx';
import {createProbeAction} from './task-probe-action.jsx';

const MODAL_EXIT_MS = 180;
const motionExitDelay = duration => {
  if (typeof window === 'undefined') return duration;
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 1 : duration;
};

const asList = value => Array.isArray(value) ? value.filter(Boolean) : [];
const displayText = (value, fallback='') => typeof value === 'string' && value.trim() ? value : fallback;
const displayName = (value, fallback='抖音账号') => displayText(value, fallback);
const initial = (value, fallback='·') => displayName(value, fallback).slice(0, 1);
const redemptionPlaintext = code => typeof code === 'string' ? code : displayText(code?.code || code?.full_code || code?.plaintext || code?.code_plaintext || code?.value, '');
const formatAdminDate = value => value ? new Date(value).toLocaleString('zh-CN', {dateStyle:'medium', timeStyle:'short'}) : '—';
const interactionRequestId = () => globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r=Math.random()*16|0; return (c==='x'?r:(r&3|8)).toString(16); });
async function sendCloudAction(scan,data,kind,payload) {
  const form=new FormData();form.append('csrf_token',data?.csrf||'');form.append('kind',kind);form.append('request_id',interactionRequestId());form.append('action_json',JSON.stringify(payload));
  let state=await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:form});
  for(let attempt=0;attempt<20&&['queued','processing'].includes(state?.status);attempt++){await new Promise(resolve=>setTimeout(resolve,400));state=await api(state.status_url)}
  return state;
}
const pendingMembershipPlan = membership => {
  const candidate = membership?.pending_subscription || membership?.pending_plan || membership?.next_subscription || membership?.next_plan;
  return candidate && typeof candidate === 'object' ? candidate : null;
};
const membershipMaxTasks = value => {
  const raw = value?.max_tasks ?? value?.task_limit ?? value?.tasks_limit;
  return raw == null || raw === '' ? null : (Number.isFinite(Number(raw)) ? Number(raw) : null);
};
const membershipIsFreeTier = value => Boolean(value?.free_tier)
  || (value?.free_tier == null && ['inactive','expired','revoked'].includes(value?.status) && !value?.active && membershipMaxTasks(value) === 1);
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
const UNLIMITED_TASKS = 2147483647;
const planDurationDays = plan => Number(plan?.duration_days) || 30;
const planMaxTasks = plan => Number(plan?.max_tasks) || UNLIMITED_TASKS;
const planBillingLabel = plan => plan?.billing_label || (planDurationDays(plan) >= 180 ? '半年' : '月');
const planDurationLabel = plan => plan?.duration_label || (planDurationDays(plan) >= 180 ? '6 个月' : '1 个月');
const planTaskLabel = plan => plan?.task_limit_label || (planMaxTasks(plan) >= UNLIMITED_TASKS ? '不限好友任务' : `${planMaxTasks(plan)} 个好友任务`);
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

const previewParams=new URLSearchParams(window.location.search);
const previewRequested=import.meta.env.DEV&&(previewParams.has('preview')||sessionStorage.getItem('dyfire-local-preview')==='1');
if(previewRequested){sessionStorage.setItem('dyfire-local-preview','1');if(previewParams.has('admin'))sessionStorage.setItem('dyfire-preview-role','admin');else if(previewParams.has('user'))sessionStorage.setItem('dyfire-preview-role','user');}
const previewAdmin=previewRequested&&sessionStorage.getItem('dyfire-preview-role')==='admin';
const previewTaskChanges=new Map();
const previewUserChanges=new Map();
const previewMembershipChanges=new Map();
const previewRemovedTasks=new Set();
const previewDate=days=>new Date(Date.now()+days*86400000).toISOString();
let previewScan = null;
let previewCreatedTasks = [];
let previewAttention = [{id:'preview-attention-1',title:'发送待确认 · 小林',detail:'请先核对抖音聊天记录，不要直接重发。',url:'/runs',at:'2026-09-28T09:45:00+08:00',tone:'warning',read:false}];

function previewData() {
  const account = {id:'preview-account', name:'主账号', profile:{nickname:'火花用户'}, health:{state:'valid', label:'登录有效',checked_at:previewDate(-.03)}, validation_state:'valid'};
  const activityDays = Array.from({length:365},(_,i)=>{const date=new Date();date.setHours(12,0,0,0);date.setDate(date.getDate()-(364-i));return {date:date.toISOString().slice(0,10),count:(i*7)%6};});
  const secondAccount = {id:'preview-account-2', name:'副账号', profile:{nickname:'备用火花'}, health:{state:'verification', label:'需要重新验证',blocked:true,checked_at:previewDate(-.05),reason:'请打开原账号页面，按抖音提示完成验证'}, validation_state:'verification'};
  const thirdAccount = {id:'preview-account-3', name:'测试账号', profile:{nickname:'小号'}, health:{state:'valid', label:'登录有效',checked_at:previewDate(-.03)}, validation_state:'valid'};
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
    user:{name:'演示用户', role:previewAdmin?'admin':'user'}, csrf:'preview-csrf',
    accounts:[account,secondAccount,thirdAccount], tasks:[...previewCreatedTasks,...tasks].filter(task=>!previewRemovedTasks.has(task.id)).map(task=>({...task,...previewTaskChanges.get(task.id)})), runs:runs.map((run,index)=>({...run,taskId:tasks[index].id,time:index===5?'今天 08:20':run.time})), recentRuns:runs.map((run,index)=>({id:run.id,target:run.name,owner:'演示用户',time:run.time,status:run.label,tone:run.status==='success'?'good':run.status==='running'?'warn':'danger'})),
    membership:{active:true, can_add_task:true, max_accounts:3, max_tasks:UNLIMITED_TASKS, task_limit_label:'不限好友任务', task_count:6, enabled_task_count:5, task_overage:0, days_remaining:27, valid_until:previewDate(27), status_label:'月度畅享 · 使用中'},
    plans:[{id:'monthly',name:'月度畅享',price_yuan:'5',duration_days:30,duration_label:'1 个月',billing_label:'月',max_accounts:1,max_tasks:UNLIMITED_TASKS,task_limit_label:'不限好友任务'},{id:'half_year',name:'半年畅享',price_yuan:'20',duration_days:180,duration_label:'6 个月',billing_label:'半年',max_accounts:1,max_tasks:UNLIMITED_TASKS,task_limit_label:'不限好友任务'}],
    activity:{period_days:365,total:activityDays.reduce((sum,day)=>sum+day.count,0),days:activityDays},
    counts:{users:18, accounts:26, tasks:74, runs:1260},
    users:[{id:'preview-user-1',username:'演示用户',role:'user',status:'active',created:'2026-09-01',account_count:1,task_count:3,membership_label:'有效订阅',account_health_status:'需处理',account_health_label:'1 个账号需复核',today_success:2,today_failed:1},{id:'preview-user-2',username:'管理员',role:'admin',status:'active',created:'2026-08-21',account_count:2,task_count:8,membership_label:'管理员',account_health_status:'正常',account_health_label:'账号正常',today_success:8,today_failed:0}],
    subscriptions:[{id:'preview-user-1',username:'演示用户',account_count:1,task_count:3,membership_status:'active',membership_label:'有效',valid_until:'2026-10-18',max_accounts:2,max_tasks:10}],
    redemptionCodes:[{id:'preview-code-1',code:'DYFIRE-DEMO-30-ABCD',status:'待兑换',redeemed_at:null,expires_at:null,max_accounts:1,max_tasks:10,task_limit_label:'10'}],
    groups:[{owner_id:'preview-user-1',account_id:account.id,username:'演示用户',remark:'我的抖音账号',nickname:'火花用户',total:3,enabled:2,validation_state:'valid'}],
    operations:{refresh_interval_seconds:30,system:{scope_label:'本地预览服务器',cpu:{percent:18},memory:{percent:42},disk:{percent:37}},queue:{due_tasks:2,running_runs:1,retrying_runs:0,attention_24h:2,active_logins:0},database:{size_bytes:5242880},services:{worker:{status:'healthy',label:'运行中'}}},
    audit:[{id:'preview-audit-1',event:'任务发送',subject_type:'task_run',created_at:'2026-09-21T09:30:00+08:00',detail:'小琪 · 已发出'}],
    mail:{enabled:false,host:'',port:465,mode:'ssl',username:'',sender:'',sender_name:'火花守护',password_configured:false,events:[],deliveries:[]},
  };
}

function mockApi(path, options={}) {
  const data = previewData();
  if(path.startsWith('/api/app/tasks?')){const params=new URLSearchParams(path.split('?')[1]);const items=data.tasks.filter(task=>!params.get('account_id')||task.accountId===params.get('account_id'));return Promise.resolve({items,pagination:{page:1,total:items.length,has_next:false,has_previous:false,pages:1}})}
  data.users=data.users.map(user=>({...user,...previewUserChanges.get(user.id)}));
  data.user.id=previewAdmin?'preview-user-2':'preview-user-1';
  const userMutation=path.match(/^\/api\/admin\/users\/([^/]+)\/(note|role|toggle|reset-password)$/);
  if(userMutation&&options.method==='POST') {
    const [,id,action]=userMutation, body=JSON.parse(options.body||'{}');
    const user=data.users.find(item=>item.id===id)||{};
    if(action==='reset-password')return Promise.resolve({temporary_password:'Demo-Only-7392'});
    const patch=action==='note'?{admin_note:body.admin_note}:action==='role'?{role:body.role}:{status:user.status==='active'?'disabled':'active'};
    previewUserChanges.set(id,{...previewUserChanges.get(id),...patch});return Promise.resolve({...user,...patch});
  }
  const quotaMutation=path.match(/^\/api\/admin\/subscriptions\/([^/]+)\/(limits|revoke)$/);
  if(quotaMutation&&options.method==='POST') {
    const [,id,action]=quotaMutation, body=JSON.parse(options.body||'{}');
    const value={...data.membership,...previewMembershipChanges.get(id),...(action==='revoke'?{active:false,status_label:'订阅已撤销',valid_until:previewDate(0)}:{max_accounts:body.max_accounts,max_tasks:body.max_tasks})};
    previewMembershipChanges.set(id,value);return Promise.resolve(value);
  }
  const renewal=path.match(/^\/admin\/users\/([^/]+)\/subscription$/);
  if(renewal&&options.method==='POST') {
    const id=renewal[1], body=options.body;
    const current={...data.membership,...previewMembershipChanges.get(id)}, days=Number(body.get('duration_days'));
    const validUntil=new Date(Math.max(Date.now(),new Date(current.valid_until).getTime())+days*86400000).toISOString();
    previewMembershipChanges.set(id,{...current,active:true,valid_until:validUntil,days_remaining:Math.ceil((new Date(validUntil)-Date.now())/86400000)});return Promise.resolve({ok:true});
  }
  const adminTask=path.match(/^\/api\/admin\/tasks\/([^/]+)(?:\/(enabled))?$/);
  if(adminTask&&['PATCH','POST'].includes(options.method)) {
    const [,id,action]=adminTask,body=JSON.parse(options.body||'{}');
    previewTaskChanges.set(id,{...previewTaskChanges.get(id),...(action==='enabled'?{enabled:body.enabled}:{time:body.send_time,message:body.message_template,kind:body.message_kind==='douyin_sticker'?'续火花表情':'文字消息'})});return Promise.resolve({ok:true});
  }
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
  if (path === '/api/app/bootstrap' || path === '/api/admin/bootstrap' || path.startsWith('/api/admin/bootstrap?')) return Promise.resolve(data);
  if (path.startsWith('/api/admin/users/') && path.endsWith('/workspace')) {
    const userId=path.split('/')[4]||data.users[0]?.id;
    const user=data.users.find(item=>item.id===userId)||data.users[0]||{};
    return Promise.resolve({user,membership:{...data.membership,...previewMembershipChanges.get(userId)},accounts:data.accounts,tasks:data.tasks,runs:data.runs});
  }
  if (path.startsWith('/api/admin/redemption-codes')) return Promise.resolve({items:data.redemptionCodes,pagination:{page:1,page_size:20,total:data.redemptionCodes.length,pages:1,has_previous:false,has_prev:false,has_next:false}});
  if (path === '/api/notifications') return Promise.resolve({csrf:data.csrf,mail_ready:true, preference:{email:'',enabled:false,verified:false}, policy:{events:['account_alert','account_recovered','task_failed','expiry'],quiet_start:23,quiet_end:8,quiet_enabled:false,interval_minutes:60}});
  if (path === '/api/attention') return Promise.resolve({csrf:data.csrf,items:previewAttention});
  if (path === '/api/account-center') return Promise.resolve({csrf:data.csrf,current_session_id:'preview-session-1',sessions:[{id:'preview-session-1',created_at:'2026-09-28T08:00:00+08:00',expires_at:'2026-09-28T16:00:00+08:00',current:true},{id:'preview-session-2',created_at:'2026-09-26T19:00:00+08:00',expires_at:'2026-09-27T03:00:00+08:00',current:false}]});
  if (path === '/api/subscription') return Promise.resolve({csrf:data.csrf,membership:{...data.membership,valid_until:previewDate(27),account_count:3},grants:[{id:'preview-grant-1',source:'redemption_code',valid_from:previewDate(-3),valid_until:previewDate(27),created_at:'2026-08-28T00:00:00+08:00'}]});
  if (path === '/api/attention/read' && options.method === 'POST') { const body=JSON.parse(options.body||'{}');previewAttention=previewAttention.map(item=>body.item_id==='all'||item.id===body.item_id?{...item,read:true}:item);return Promise.resolve({ok:true,items:previewAttention}); }
  if (path === '/api/account-center/revoke-sessions' && options.method === 'POST') return Promise.resolve({ok:true,message:'其他登录会话已退出。'});
  if (path === '/api/subscription/redeem' && options.method === 'POST') { const body=JSON.parse(options.body||'{}');if(!body.code)return Promise.resolve({ok:false,message:'请输入兑换码'});return Promise.resolve({ok:true,message:'预览模式：兑换操作已模拟'}); }
  if (path === '/notifications/verify-send' || path === '/notifications/verify' || path === '/notifications/save' || path === '/notifications/policy') return Promise.resolve({message:path.endsWith('verify-send')?'验证邮件已排队，请在 10 分钟内填写验证码。':path.endsWith('/verify')?'邮箱验证成功，已开启提醒。':'设置已保存'});
  if (path === '/api/admin/updates') return Promise.resolve({configured:true,current:'0.3.40',online:true,available:null,update_available:false,rollback_versions:[]});
  if(path.startsWith('/api/admin/insights?')){
    const days=Number(new URLSearchParams(path.split('?')[1]).get('days'))===30?30:7;
    const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
    const end=Date.parse(`${today}T00:00:00Z`);
    const trend=Array.from({length:days},(_,index)=>{const success=12+index%6,failed=index%5===0?1:0,skipped=index%9===0?1:0;return {date:new Date(end-(days-1-index)*86400000).toISOString().slice(0,10),success,failed,skipped,total:success+failed+skipped}});
    return Promise.resolve({trend,alerts:{accounts:[],failed_runs:[],expiring:[]}});
  }
  if (path === '/api/admin/diagnostics') return Promise.resolve({items:data.runs.map((run,index)=>({id:run.id,name:run.name,status:run.status,stage:run.stage,attempt:1,total:index===2?18:7,error_summary:run.error||''}))});
  if (path.startsWith('/admin/operations/history')) return Promise.resolve({hours:24,state:'fresh',bucket_seconds:3600,start:Math.floor(Date.now()/1000)-86400,latest_at:Math.floor(Date.now()/1000),points:Array.from({length:24},(_,i)=>({at:Math.floor(Date.now()/1000)-(23-i)*3600,cpu:18+(i%5)*3,memory:42+(i%4)*2,disk:37,load:0.2,logins:0,sends:0})),cpu_peak:30,memory_peak:48});
  if (path.startsWith('/api/admin/tasks/groups/')) return Promise.resolve({items:data.tasks.map(task=>({...task,account:'我的抖音账号',username:'演示用户'}))});
  if (path.includes('/conversations')) return Promise.resolve({items:[
    {name:'小琪',nickname:'小琪',remark_name:'工作伙伴',sec_uid:'preview-friend-1'},
    {name:'阿杰',nickname:'阿杰',remark_name:'',sec_uid:'preview-friend-2'},
    {name:'郭琪',nickname:'郭琪',remark_name:'郭琪',sec_uid:'preview-friend-3'},
    {name:'小林',nickname:'林林',remark_name:'',sec_uid:'preview-friend-4'},
    {name:'小雨',nickname:'小雨',remark_name:'同事',sec_uid:'preview-friend-5'},
    {name:'阿南',nickname:'阿南',remark_name:'',sec_uid:'preview-friend-6'},
  ]});
  if (path.startsWith('/api/studio/runs')) {const params=new URLSearchParams(path.split('?')[1]);const status=params.get('status'),q=params.get('q')||'';const rows=data.runs.filter(run=>(!status||run.status===status)&&run.name.includes(q));const pages=Math.max(1,Math.ceil(rows.length/12)),page=Math.min(pages,Math.max(1,Number(params.get('page'))||1));return Promise.resolve({items:rows.slice((page-1)*12,page*12),total:rows.length,page,pages});}
  if (/^\/tasks\/[^/]+\/(edit|toggle|delete)$/.test(path)) {
    const [, , id,action]=path.split('/');const task=data.tasks.find(row=>row.id===id);const body=options.body;
    if(action==='edit')previewTaskChanges.set(id,{...previewTaskChanges.get(id),time:body.get('send_time'),kind:body.get('message_kind')==='text'?'文字消息':'续火花表情',message:body.get('message_template')});
    if(action==='toggle')previewTaskChanges.set(id,{...previewTaskChanges.get(id),enabled:!task?.enabled});
    if(action==='delete')previewRemovedTasks.add(id);
    return Promise.resolve({ok:true});
  }
  if(path.startsWith('/api/admin/users?'))return Promise.resolve({items:data.users,pagination:{page:1,pages:1,total:data.users.length}});
  if(path==='/subscription/preview')return Promise.resolve({preview:{duration_days:30,max_accounts:1,max_tasks:10,valid_until:previewDate(57),quota_effect:'after_current_term'}});
  if(path==='/api/admin/operations'||path==='/admin/operations/snapshot')return Promise.resolve(data.operations);
  if (path === '/accounts/scan'||path==='/accounts/recovery') { previewScan={id:'preview-scan',mode:path.endsWith('recovery')?'recovery':'bind',account_id:options.body?.get('account_id')||null,status:'waiting',message:'请使用抖音 App 扫码并在手机确认'}; return Promise.resolve(previewScan); }
  if (path.startsWith('/accounts/scan/preview-scan/qr')) return Promise.reject(new Error('预览模式不提供真实二维码'));
  if (path.startsWith('/accounts/scan/preview-scan')) return Promise.resolve(previewScan||{id:'preview-scan',status:'waiting',message:'预览登录流程'});
  if (path.startsWith('/admin/settings/mail/')) return Promise.resolve({message:'预览模式：设置未提交到服务器'});
  return Promise.resolve({ok:true, message:'预览模式：操作已模拟'});
}

const api=createApiClient({preview:previewRequested,mock:mockApi,dev:import.meta.env.DEV});
let businessModule;
const loadBusiness=()=>businessModule??=(import('./business-panels.jsx').then(module=>module.createBusinessPanels({api,Card,Badge,Button,interactionRequestId})));
const AnnouncementInbox=React.lazy(()=>loadBusiness().then(module=>({default:module.AnnouncementInbox})));
const AdminBusinessPanel=React.lazy(()=>loadBusiness().then(module=>({default:module.AdminBusinessPanel})));
const TaskProbeAction=createProbeAction({api});
const Admin=React.lazy(()=>import('./admin-pages.jsx').then(module=>({default:module.createAdminPages({AccountIncidents,asList,initial,redemptionPlaintext,formatAdminDate,pendingMembershipPlan,membershipTaskLimitLabel,membershipOverLimit,membershipDate,UNLIMITED_TASKS,planDurationDays,planMaxTasks,planDurationLabel,planTaskLabel,api,AdminBusinessPanel,useDebounced,Badge,Card,Empty,confirmModal,LoadingState,PageHeader,Metric,MembershipPlanNotice,SupportReference})})));
const {ScanVerification,CloudInteraction,CloudFrame,BindingProgress}=createBindingComponents({api,Button,Drawer,interactionRequestId,sendCloudAction});

// ── Utility hooks ────────────────────────────────────────────────────────────


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

// MagneticButton: wraps a button so it gently follows the cursor on fine
// pointer devices. The offset is kept in a custom property so the component's
// normal hover/pressed transform remains available to CSS.
function MagneticButton({children, className='', strength=10, ...props}) {
  const ref = useRef(null);
  const x = useRef(0);
  const y = useRef(0);
  const motionEnabled = useRef(false);
  useEffect(() => {
    const media = window.matchMedia?.('(hover: hover) and (pointer: fine)');
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const sync = () => { motionEnabled.current = Boolean(media?.matches && !reduced?.matches); };
    sync();
    media?.addEventListener?.('change', sync);
    reduced?.addEventListener?.('change', sync);
    return () => {
      media?.removeEventListener?.('change', sync);
      reduced?.removeEventListener?.('change', sync);
    };
  }, []);
  const onMove = e => {
    if (!ref.current || !motionEnabled.current) return;
    const rect = ref.current.getBoundingClientRect();
    x.current = ((e.clientX - rect.left) / rect.width - 0.5) * strength * 2;
    y.current = ((e.clientY - rect.top) / rect.height - 0.5) * strength * 2;
    ref.current.style.setProperty('--magnetic-x', `${x.current}px`);
    ref.current.style.setProperty('--magnetic-y', `${y.current}px`);
  };
  const onLeave = () => {
    if (!ref.current) return;
    ref.current.style.setProperty('--magnetic-x', '0px');
    ref.current.style.setProperty('--magnetic-y', '0px');
  };
  return (
    <button
      ref={ref}
      className={`button magnetic-button ${className}`}
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
function Badge({children, tone='neutral', className=''}) { return <span className={`badge ${tone}${className?` ${className}`:''}`}><i />{children}</span>; }
// Compatibility wrapper for the styled native select overlay.
function Select({value, onChange, children, className='', ...props}) {
  return <select className={className} value={value} onChange={onChange} {...props}>{children}</select>;
}
// Keep native selects as the form source of truth, but render their popup in
// the workspace visual language. This avoids browser-specific grey option
// sheets while preserving React's normal value/change contract.
function Card({children, className='', interactive=false, ...props}) {
  const ref = useRef(null);
  const trackPointer = event => {
    if (event.pointerType === 'touch' || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    ref.current.style.setProperty('--spot-x', `${event.clientX - rect.left}px`);
    ref.current.style.setProperty('--spot-y', `${event.clientY - rect.top}px`);
  };
  return <section data-reveal="section" {...props} ref={ref} className={`card ${interactive?'card-interactive':''} ${className}`} onPointerMove={interactive?trackPointer:undefined}>{children}</section>;
}
function Empty({title, text, action, icon}) { return <div className="empty"><div className="empty-mark" aria-hidden="true">{icon||_si(<><rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 12h5l2 3h4l2-3h5M8 2v6M16 2v6"/></>,{width:24,height:24})}</div><h3>{title}</h3>{text && <p>{text}</p>}{action}</div>; }
function ContactQr({large=false}) {
  return <div className={`contact-qr-frame ${large?'contact-qr-large':''}`} role="img" aria-label="微信客服二维码">
    <img className="contact-qr-image contact-qr-light" src="/assets/wechat-support-qr-light.png" alt="微信客服二维码" />
    <img className="contact-qr-image contact-qr-dark" src="/assets/wechat-support-qr-dark.png" alt="微信客服二维码" aria-hidden="true" />
    <small>微信扫一扫</small>
  </div>;
}
function ContactSupportCard({compact=false, onOpen, plans=[]}) {
  return <Card className={`contact-support-card ${compact?'is-compact':''}`}>
    <div className="contact-support-copy">
      <span className="eyebrow">WECHAT SUPPORT</span>
      <h2>联系客服开通订阅</h2>
      <p>扫码添加微信，备注“火花 + 你的用户名”，客服会帮你选择套餐并开通。</p>
      <div className="contact-support-plans">{plans.map(plan=><span key={plan.id}>{plan.price_yuan} 元 · {planDurationLabel(plan)} · {planTaskLabel(plan)}</span>)}</div>
      {onOpen&&<Button variant="quiet" onClick={onOpen}>查看二维码</Button>}
    </div>
    <ContactQr />
  </Card>;
}
function ContactSupportModal({open, onClose, plans=[]}) {
  return <Drawer open={open} onClose={onClose} title="联系客服开通订阅" eyebrow="WECHAT SUPPORT" centered><div className="contact-support-modal"><ContactQr large/><h3>添加微信后备注你的用户名</h3><p>{plans.map(plan=>`${plan.price_yuan} 元 / ${planDurationLabel(plan)} · ${planTaskLabel(plan)}`).join('；')}。免费版和订阅到期后，可保留 1 个好友任务。</p><Button variant="quiet" onClick={onClose}>返回</Button></div></Drawer>;
}
function OnboardingGuide({data, membership, accounts, tasks, onNavigate}) {
  const [dismissed,setDismissed]=useState(Boolean(data?.onboarding?.dismissed));
  const [saving,setSaving]=useState(false);
  const [notice,setNotice]=useState('');
  useEffect(()=>setDismissed(Boolean(data?.onboarding?.dismissed)),[data?.onboarding?.dismissed]);
  const steps=[
    {key:'account',title:'绑定抖音账号',text:'扫码登录并完成抖音安全验证，免费版也可以直接开始。',done:accounts.length>0,href:'/accounts',action:accounts.length?'查看账号':'绑定账号'},
    {key:'friends',title:'创建续火任务',text:'免费版可先创建 1 个好友任务，更多好友再开通订阅。',done:tasks.length>0,href:'/studio',action:tasks.length?'管理任务':'创建任务'},
    {key:'runs',title:'核对第一次执行',text:'查看发送结果，确认任务按预期运行。',done:hasConfirmedExecution(data),href:'/runs',action:'查看记录'},
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
  const [closing,setClosing]=useState(false);
  const activeRef=useRef(null);
  const dialogRef=useRef(null);
  const queueRef=useRef([]);
  const showNext=useRef(()=>{});
  const closeTimerRef=useRef(null);
  const finishRef=useRef(null);
  showNext.current=()=>{
    const next=queueRef.current.shift();
    if(!next){activeRef.current=null;setRequest(null);return;}
    activeRef.current=next;
    setClosing(false);
    setRequest(next.options);
  };
  const ask=useRef(options=>new Promise(resolve=>{
    queueRef.current.push({options,resolve});
    if(!activeRef.current)showNext.current();
  }));
  useEffect(()=>{
    const bridge=options=>ask.current(options);
    window.__dyfireModalRequest=bridge;
    return()=>{
      if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);
      if(window.__dyfireModalRequest===bridge)delete window.__dyfireModalRequest;
      const active=activeRef.current;
      if(active&&!active.done)active.resolve(false);
      queueRef.current.splice(0).forEach(item=>item.resolve(false));
    };
  },[]);
  const finish=value=>{
    const active=activeRef.current;
    if(!active||active.done||closing)return;
    active.done=true;
    active.resolve(value);
    setClosing(true);
    if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current=window.setTimeout(()=>{
      activeRef.current=null;
      setRequest(null);
      setClosing(false);
      showNext.current();
    },motionExitDelay(MODAL_EXIT_MS));
  };
  finishRef.current=finish;
  const prompt=Boolean(request?.kind==='prompt');
  const [value,setValue]=useState('');
  useEffect(()=>{setValue(request?.defaultValue||'')},[request]);
  useEffect(()=>{
    if(!request)return;
    const dialog=dialogRef.current;
    const previous=document.activeElement;
    const previousOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';
    const focusable=()=>[...dialog?.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])')||[]]
      .filter(element=>{const style=window.getComputedStyle(element);return style.display!=='none'&&style.visibility!=='hidden';});
    const focusTimer=window.setTimeout(()=>focusable()[0]?.focus(),0);
    const onKey=event=>{
      if(event.key==='Escape'){finishRef.current?.(request.kind==='prompt'?null:false);return;}
      if(event.key!=='Tab')return;
      const items=focusable();
      if(!items.length)return;
      const first=items[0]; const last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    };
    window.addEventListener('keydown',onKey);
    return()=>{
      window.clearTimeout(focusTimer);
      window.removeEventListener('keydown',onKey);
      document.body.style.overflow=previousOverflow;
      if(previous&&typeof previous.focus==='function') previous.focus();
    };
  },[request]);
  if(!request)return null;
  const submit=event=>{event.preventDefault();if(prompt&&!value.trim())return;finish(prompt?value:true)};
  return createPortal(
    <div className={`modal-overlay${closing?' is-closing':''}`} onMouseDown={e=>e.target===e.currentTarget&&finish(prompt?null:false)}>
      <div ref={dialogRef} className={`modal-dialog${prompt?' modal-dialog-prompt':''}${closing?' is-closing':''}`} role="dialog" aria-modal="true" aria-labelledby="modal-dialog-title">
        <header className="modal-dialog-head">
          {request.eyebrow&&<span className="eyebrow">{request.eyebrow}</span>}
          <h2 id="modal-dialog-title">{request.title||'请确认'}</h2>
        </header>
        <form className="modal-dialog-body" onSubmit={submit}>
          {request.message&&<p className="modal-host-message">{request.message}</p>}
          {prompt&&<label className="modal-host-field"><span>{request.label||'输入内容'}</span><input className="input" autoFocus type={request.inputType||'password'} autoComplete={request.autoComplete||'current-password'} value={value} onChange={event=>setValue(event.target.value)} placeholder={request.placeholder||''} required/></label>}
          <div className="setting-actions modal-host-actions"><Button variant="quiet" type="button" onClick={()=>finish(prompt?null:false)}>{request.cancelLabel||'取消'}</Button><Button type="submit" disabled={prompt&&!value.trim()}>{request.confirmLabel||'确认'}</Button></div>
        </form>
      </div>
    </div>,
    document.body
  );
}
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
    const check=()=>{if(document.visibilityState==='hidden')return;return api('/health/ready',{timeoutMs:5000}).then(value=>{
      if(!cancelled)setState(value?.status==='ready'?'healthy':'degraded');
    }).catch(()=>{if(!cancelled)setState('degraded')});};
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
  const isActive = href => location.pathname === href || location.pathname.startsWith(`${href}/`)
    || (href === '/studio' && location.pathname.startsWith('/tasks'))
    || (href === '/admin/users' && (['/admin/subscriptions','/admin/tasks'].includes(location.pathname)||location.pathname.startsWith('/admin/tasks/groups/')||location.pathname.startsWith('/admin/customers/')))
    || (href === '/admin/runs' && location.pathname==='/admin/today')
    || (href === '/admin/operations' && ['/admin/diagnostics','/admin/audit'].includes(location.pathname));
  const dockRoutes=getMobileDockRoutes(routes,adminRoutes,admin);
  // Keep the purchase path next to the home entry so new users can reach it
  // before setting up accounts or tasks.
  const topRoutes=admin?[...routes.slice(0,3),...adminRoutes]:[routes[0],routes[4],...routes.slice(1,4)];
  const activeTopRoute=topRoutes.find(([href])=>isActive(href))?.[0] ?? null;
  const topNavItems=topRoutes.map(([href,label,IconComp])=>({value:href,label,icon:<span className="fire-nav-segment-icon" aria-hidden="true"><IconComp/></span>}));
  useEffect(()=>{document.documentElement.dataset.theme=theme; document.documentElement.style.colorScheme=theme; localStorage.setItem('dyfire-theme',theme); document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='light'?'#eeeae5':'#080608')},[theme]);

  useEffect(()=>{
    document.querySelectorAll('input.search-input,input.compact-search,.admin-run-toolbar input').forEach(input=>{
      if(!input.getAttribute('aria-label')) input.setAttribute('aria-label',input.getAttribute('placeholder')||'搜索');
    });
  },[location.pathname]);
  useEffect(()=>{if (location.state?.toast) { setToast(location.state.toast); navigate(location.pathname,{replace:true,state:{}}); }},[location,navigate]);
  const logout = () => { const form=document.createElement('form'); form.method='POST'; form.action='/logout'; const input=document.createElement('input'); input.name='csrf_token'; input.value=data?.csrf||''; form.append(input); document.body.append(form); form.submit(); };
  const dockLink=([href,label,IconComp])=><Link key={href} className={isActive(href)?'active':''} aria-current={isActive(href)?'page':undefined} to={href}><IconComp/><small>{label}</small></Link>;
  return <div className="app-shell fire-shell"><RouteScroll/>
    <main className="main-shell fire-main" id="workspace"><header className="fire-nav"><div className="fire-nav-brand"><Logo/></div><RubberSegment className="fire-nav-segment" items={topNavItems} value={activeTopRoute} onChange={href=>navigate(href)} trackColor={theme==='light'?'rgba(255,255,255,.62)':'rgba(255,255,255,.035)'} thumbColor={theme==='light'?'#e55b20':'#ff6b25'} textColor={theme==='light'?'#74645b':'#a88f83'} activeTextColor="#20100a" size="md" radius={999} inset={5} equalSlots stretch={65} squash={2} speed={1.15} glide={75} draggable aria-label={admin?'管理导航':'工作区导航'}/><div className="fire-nav-right"><ServiceIndicator operations={data?.operations}/><button type="button" className="fire-theme" onClick={()=>setTheme(theme==='dark'?'light':'dark')} aria-label="切换主题"><ThemeIcon light={theme==='light'}/></button><UserNavigation name={userName} admin={admin} routes={routes} adminRoutes={adminRoutes} onLogout={logout}><span className="avatar avatar-small">{initial(userName).toUpperCase()}</span><span>{admin?'管理员':userName}</span></UserNavigation></div></header><div className="main-inner"><RouteMotionDiv locationKey={location.pathname}>{children}</RouteMotionDiv></div></main>
    <nav className="mobile-dock" aria-label="移动端主导航">{dockRoutes.map(dockLink)}</nav><Toast message={toast} onClose={()=>setToast('')}/><ModalHost/>
  </div>;
}

function PageHeader({title, text, actions}) { return <header data-reveal="heading" className="page-header"><div><h1>{title}</h1>{text&&<p>{text}</p>}</div>{actions&&<div className="page-actions">{actions}</div>}</header>; }
function Metric({label,value,detail,tone=''}) {
  const textValue=typeof value==='string'&&/^\p{L}/u.test(value);
  return <Card className="metric"><span>{label}</span><strong className={`${tone} ${textValue?'metric-value-text':''}`}><LiveNumber value={value}/></strong>{detail&&<small>{detail}</small>}</Card>;
}
function AccountStatus({account={}, onAction}) { const health=account.health||{}; const name=displayName(account.name); const good=health.state==='valid'||account.validation_state==='valid'; const tone=good?'good':(health.state==='verification'||health.blocked?'danger':'warn'); return <div className="account-status-row"><span className="avatar">{account.profile?.avatar_url?<img src={account.profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><b>{name}</b><small>{account.profile?.nickname && account.profile.nickname!==name?account.profile.nickname+' · ':''}{health.label||'等待复核'}</small></div><Badge tone={tone}>{health.label||'等待复核'}</Badge><Button variant="quiet" onClick={()=>onAction?.(account)}>{good?'查看':'处理'}</Button></div>; }

function Dashboard({data,onRefresh}) {
  const navigate=useNavigate(); const accountNavigate=useAccountNavigation();
  const tasks=asList(data?.tasks), accounts=asList(data?.accounts), runs=asList(data?.runs), membership=data.membership||{};
  const [supportOpen,setSupportOpen]=useState(false), [saved,setSaved]=useState(''), [editTask,setEditTask]=useState(null);
  const runByTaskId=new Map(); runs.forEach(run=>{if(run?.taskId&&!runByTaskId.has(run.taskId))runByTaskId.set(run.taskId,run)});
  const friends=tasks.filter(task=>task.enabled||runByTaskId.has(task.id));
  const create=()=>navigate(accounts.length?'/studio':'/accounts');
  return <div className="fire-home home-refined">
    <React.Suspense fallback={null}><AnnouncementInbox data={data}/></React.Suspense>
    <div className="fire-home-toolbar"><div className="home-title"><span className="fire-date">{new Date().toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',weekday:'long'})} · 北京时间</span><h1>今天的续火</h1></div><div className="fire-home-actions"><Button variant="quiet" onClick={()=>navigate('/runs')}>执行记录</Button><Button onClick={create}>{accounts.length?'新建任务':'绑定账号'}</Button></div></div>
    <DailyOverview accounts={accounts} runs={runs} tasks={tasks} limits={data.resource_limits} onAccountTasks={(account,event)=>accountNavigate(account,event?.currentTarget?.closest('.daily-account-row'))} onCreate={create}/>
    <OnboardingGuide data={data} membership={membership} accounts={accounts} tasks={tasks} onNavigate={navigate}/>
    <div className="fire-home-main"><section><div className="home-task-heading"><h2>续火花任务</h2><Link to="/studio" className="text-action">全部任务<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div><div className={`fire-friend-grid${friends.length<=3?' is-compact':''}`}>{friends.length?friends.slice(0,9).map(task=><FriendCard key={task.id} task={task} last={runByTaskId.get(task.id)} state={friendState(task,runByTaskId.get(task.id))} onEdit={setEditTask}/>):<Empty title="还没有今日续火任务" text="创建任务后会显示今日执行状态。"/>}{friends.length<3&&<button type="button" data-reveal="section" className="fire-friend-card fire-friend-add" onClick={create}><span>＋</span><b>添加目标好友</b><small>从已同步会话中选择</small></button>}</div></section></div>
    <div className="fire-home-bottom"><ActivityHeatmap activity={data.activity}/><section data-reveal="section" className="home-membership"><div><h2>{membershipIsFreeTier(membership)?'免费版':'我的订阅'}</h2><p>启用任务 {membershipTaskUsage(membership)??membership.task_count??data.resource_limits?.tasks_enabled_total??tasks.filter(task=>task.enabled).length} / {membership.task_limit_label||membership.max_tasks||1}</p></div><strong>{membership.active?(membership.days_remaining??'∞'):membershipIsFreeTier(membership)?'1':'—'}<small>{membership.active?'天有效':membershipIsFreeTier(membership)?'个免费任务':'未开通'}</small></strong><Button variant="quiet" onClick={()=>navigate('/subscription')}>{membership.active?'管理订阅':membershipIsFreeTier(membership)?'升级订阅':'查看套餐'}</Button><MembershipPlanNotice membership={membership} compact/>{!membership.active&&!membershipIsFreeTier(membership)&&<button type="button" className="fire-support-link" onClick={()=>setSupportOpen(true)}>联系客服开通</button>}</section></div>
    <TaskEditDrawer task={editTask} data={data} open={Boolean(editTask)} onClose={()=>setEditTask(null)} onRefresh={onRefresh} onSaved={(message,id)=>{setSaved(message);flashTask(id)}}/><Toast message={saved} onClose={()=>setSaved('')}/><ContactSupportModal plans={data.plans||[]} open={supportOpen} onClose={()=>setSupportOpen(false)}/>
  </div>;
}

function friendState(task,last){
  if(last?.status==='success')return {label:'已完成',tone:'good'};
  if(last?.status==='failed')return {label:'失败',tone:'danger'};
  if(last?.status==='submitted_unconfirmed')return {label:'待核实',tone:'warn'};
  if(last?.status==='running'||last?.status==='retrying')return {label:'执行中',tone:'warn'};
  return task?.enabled?{label:'待发送',tone:'warn'}:{label:'已暂停',tone:'neutral'};
}

function compactFriendSchedule(value, fallback) {
  const raw = String(value || fallback || '').trim();
  if (!raw) return '今日 —';
  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})/);
  const timeMatch = raw.match(/(?:T|\s|^)(\d{1,2}):(\d{2})(?::\d{2})?/);
  const time = match
    ? `${match[4].padStart(2, '0')}:${match[5]}`
    : timeMatch
      ? `${timeMatch[1].padStart(2, '0')}:${timeMatch[2]}`
      : raw.slice(-5);
  if (!match) return `今日 ${time}`;

  const localDay = `${match[1]}-${match[2]}-${match[3]}`;
  const today = new Date();
  const dayKey = date => date.toLocaleDateString('en-CA', {timeZone:'Asia/Shanghai'});
  const todayKey = dayKey(today);
  const tomorrowKey = dayKey(new Date(today.getTime() + 86400000));
  const dayLabel = localDay === todayKey
    ? '今日'
    : localDay === tomorrowKey
      ? '明日'
      : `${Number(match[2])}月${Number(match[3])}日`;
  return `${dayLabel} ${time}`;
}

// FriendCard with pointer-tracking spotlight (same pattern as Card)
function FriendCard({task: t, last, state, onEdit}) {
  const ref = useRef(null);
  const reduced=useReducedMotion();
  const track = e => {
    if (e.pointerType === 'touch' || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    ref.current.style.setProperty('--spot-x', `${e.clientX - r.left}px`);
    ref.current.style.setProperty('--spot-y', `${e.clientY - r.top}px`);
  };
  const inner = <>
    <div className="fire-friend-top">
      <motion.span layoutId={reduced?undefined:`task-avatar-${t.id}`} className="fire-friend-avatar" transition={{duration:.32,ease:[.22,1,.36,1]}}>{t.avatar?<img src={t.avatar} alt=""/>:initial(t.name)}</motion.span>
      <Badge tone={state?.tone||'neutral'}>{state?.label||'待发送'}</Badge>
    </div>
    <motion.b layoutId={reduced?undefined:`task-name-${t.id}`} transition={{duration:.32,ease:[.22,1,.36,1]}}>{displayName(t.name)}</motion.b>
    {t.streak != null && <strong><span className="fire-streak-icon" aria-hidden="true">🔥</span> {t.streak}<small> 天连续</small></strong>}
    <time>{compactFriendSchedule(last?.time, t.time)}</time>
  </>;
  if (onEdit) {
    return (
      <button id={`task-row-${t.id}`} ref={ref} type="button" data-reveal="section" className="fire-friend-card" onClick={()=>onEdit(t)} onPointerMove={track}>
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

function ContactPicker({open,onClose,account,data,onDone,releasedKeys=new Set(),selectionLimit=null,limitMessage='',onLimitReached}) {
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
  return <ContactPanel DesktopDrawer={Drawer} open={open} onClose={close} title="选择目标好友" eyebrow="SELECT CONNECTIONS" wide>
    <div className="drawer-content contact-picker-content">
      <input className="input search-input" aria-label="搜索已同步好友" value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入昵称或备注，搜索已加载会话"/>
      {limitMessage&&<div className="inline-feedback" role="status">{limitMessage} <Link to="/subscription" className="text-action">查看套餐<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div>}
      <div className="drawer-tools"><span>{selected.size} 人已选{Number.isFinite(selectionLimit)?` / ${selectionLimit} 个可用额度`:''}</span><button type="button" onClick={()=>{const keys=visible.filter(i=>!existing.has(contactKey(i))&&!selected.has(contactKey(i))).map(contactKey);const next=Number.isFinite(selectionLimit)?keys.slice(0,Math.max(0,selectionLimit-selected.size)):keys;if(Number.isFinite(selectionLimit)&&keys.length>next.length)onLimitReached?.();controller.selectMany(next)}} disabled={loading||Number.isFinite(selectionLimit)&&selectionLimit<=selected.size}>全选当前结果</button><button type="button" onClick={()=>controller.clearSelection()} disabled={loading}>清空</button></div>
      {(loading||busy==='page'||busy==='sync')&&items.length>0&&<div className="contact-picker-loading" role="status" aria-live="polite"><span className="loading-spinner" aria-hidden="true"/>{busy==='sync'?'正在同步好友…':'正在加载更多好友…'}</div>}
      {loading&&!items.length?<div className="skeleton-stack"><div/><div/><div/></div>:<div className="contact-list"><MotionList className="contact-motion-list">{visible.map((item,i)=>{
        const id=contactKey(item)||`contact-${i}`;const name=displayName(item?.name,'未命名好友');const disabled=existing.has(id);
        const atLimit=Number.isFinite(selectionLimit)&&selected.size>=selectionLimit&&!selected.has(id);
        return <label className={`contact-row ${selected.has(id)?'is-selected ':''}${disabled||atLimit?'disabled':''}`} key={id} data-motion-state={selected.has(id)?'selected':'idle'}><input type="checkbox" disabled={disabled||atLimit} checked={selected.has(id)} onChange={e=>{if(e.target.checked&&atLimit){onLimitReached?.();return}controller.select(id,e.target.checked)}}/><span className="avatar">{item?.avatar_url?<img src={item.avatar_url} alt=""/>:initial(name)}</span><span className="grow"><b>{name}</b><small>{disabled?'已有续火任务':atLimit?'额度已用完':item?.remark_name||item?.nickname||'已同步会话'}</small></span></label>
      })}</MotionList></div>}
      {!loading&&!error&&!visible.length&&<Empty title={query?'没有匹配的已加载会话':'还没有已同步好友'} text={query?'搜索仅覆盖已加载会话，可以加载或同步更多好友后再搜索。':'点击下方同步按钮，读取这个账号的最近好友会话。'}/>}
    </div>
    <footer className="drawer-foot contact-picker-footer">
      <div className="contact-picker-coverage" aria-live="polite"><b>{coverageLabel}</b><small>{busy==='sync'?'同步可能需要几分钟，关闭后可重新打开查看进度。':coverageHint}</small></div>
      {error&&<div className="contact-picker-error" role="alert"><span>{error}</span>{!items.length&&<button type="button" disabled={loading||Boolean(busy)} onClick={()=>controller.retry()}>重新加载</button>}</div>}
      <div className="contact-picker-actions"><span>{selected.size} 位好友</span><Button type="button" variant="quiet" disabled={loading||Boolean(busy)} onClick={()=>controller.next()}>{actionLabel}</Button><Button type="button" disabled={!selected.size} onClick={()=>{onDone([...selected].map(id=>items.find(i=>contactKey(i)===id)).filter(Boolean));close()}}>完成选择</Button></div>
    </footer>
  </ContactPanel>;
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
  const contextLocation=useLocation();
  const reduced=useReducedMotion();
  const contextNavigate=useNavigate();
  const contextId=new URLSearchParams(contextLocation.search).get('account')||'';
  const accounts=asList(data?.accounts); const membership=data?.membership||{};
  const contextAccount=accounts.find(item=>item.id===contextId);
  const [accountId,setAccountId]=useState(contextAccount?.id||accounts[0]?.id||'');
  useEffect(()=>{if(contextAccount){setAccountId(contextAccount.id);setFriends([]);setSelectedTime('')}},[contextAccount?.id]);
  const [selectedTime,setSelectedTime]=useState('');
  const [kind,setKind]=useState('douyin_sticker'); const [message,setMessage]=useState('');
  const [friends,setFriends]=useState([]); const [picker,setPicker]=useState(false);
  const [search,setSearch]=useState(''); const [filter,setFilter]=useState('all'); const [notice,setNotice]=useState(''); const [successToast,setSuccessToast]=useState('');
  const [openTaskMenu,setOpenTaskMenu]=useState(null);
  useEffect(()=>{
    if(openTaskMenu==null)return undefined;
    const close=e=>{if(!e.target.closest?.('.task-row-menu'))setOpenTaskMenu(null)};
    const onKey=e=>{if(e.key==='Escape')setOpenTaskMenu(null)};
    document.addEventListener('pointerdown',close);
    document.addEventListener('keydown',onKey);
    return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',onKey)};
  },[openTaskMenu]);
  const [taskRows,setTaskRows]=useState(()=>asList(data?.tasks));
  const [taskPage,setTaskPage]=useState(()=>data?.tasks_pagination?.page||1);
  const [taskPagination,setTaskPagination]=useState(()=>data?.tasks_pagination||null);
  const [contextLoading,setContextLoading]=useState(Boolean(contextAccount)), [taskListError,setTaskListError]=useState('');
  const taskRead=useRef({generation:0,controller:null,scope:contextAccount?.id||''});
  const taskScope=useRef(contextAccount?.id||'');taskScope.current=contextAccount?.id||'';
  const [loadingMoreTasks,setLoadingMoreTasks]=useState(false); const [createdPending,setCreatedPending]=useState(0);
  const [releasedKeys,setReleasedKeys]=useState(()=>new Set());
  const refreshRef=useRef(onRefresh); refreshRef.current=onRefresh;
  const successRef=useRef(null);
  const rowFeedback=useRowFeedback();
  const composer=useMemo(()=>createTaskComposerController({request:api, refresh:()=>refreshRef.current?.(), onCreated:value=>{
    const added=asList(value?.items);
    setReleasedKeys(keys=>{
      if(!keys.size)return keys;
      const next=new Set(keys);
      added.forEach(item=>next.delete(contactKey(item)));
      return next;
    });
    const visibleAdded=added.filter(task=>!taskScope.current||task.accountId===taskScope.current);
    setTaskRows(rows=>mergeTaskRows(rows,visibleAdded));
    visibleAdded.forEach(task=>rowFeedback.mark(task.id,'新建任务已保存'));
    setTaskPagination(page=>page?{...page,total:page.total+visibleAdded.length}:page);
    setCreatedPending(count=>count+added.length);
    setSuccessToast(`已成功创建 ${value.count??added.length} 个续火任务`);
    setFilter('all');setSearch('');setFriends([]);setSelectedTime('');setNotice('');
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
  },[accountId,picker]);
  useEffect(()=>{
    const read=taskRead.current;read.controller?.abort();const generation=++read.generation;
    setTaskListError('');setLoadingMoreTasks(false);setCreatedPending(0);
    if(!contextAccount){read.scope='';setTaskRows(asList(data?.tasks));setTaskPage(data?.tasks_pagination?.page||1);setTaskPagination(data?.tasks_pagination||null);setContextLoading(false);return;}
    const controller=new AbortController();read.controller=controller;
    if(read.scope!==contextAccount.id){setTaskRows(asList(data.tasks).filter(task=>task.accountId===contextAccount.id));setTaskPage(1);setTaskPagination(null)}
    read.scope=contextAccount.id;setContextLoading(true);
    api(`/api/app/tasks?page=1&account_id=${encodeURIComponent(contextAccount.id)}`,{signal:controller.signal}).then(value=>{
      if(generation!==read.generation||controller.signal.aborted)return;
      setTaskRows(asList(value.items));setTaskPagination(value.pagination||null);setTaskPage(value.pagination?.page||1);
    }).catch(error=>{if(error.name!=='AbortError'&&generation===read.generation)setTaskListError(error.message)}).finally(()=>{if(generation===read.generation&&!controller.signal.aborted)setContextLoading(false)});
    return()=>read.controller?.abort();
  },[data?.tasks,data?.tasks_pagination,contextAccount?.id]);
  const loadMoreTasks=async()=>{
    if(loadingMoreTasks||contextLoading||!taskPagination?.has_next)return;
    const read=taskRead.current, generation=read.generation;const controller=new AbortController();read.controller=controller;
    setLoadingMoreTasks(true);setTaskListError('');
    try{const params=new URLSearchParams({page:String(taskPage+1)});if(contextAccount)params.set('account_id',contextAccount.id);const next=await api(`/api/app/tasks?${params}`,{signal:controller.signal});if(generation!==read.generation||controller.signal.aborted)return;setTaskRows(rows=>mergeTaskRows(rows,asList(next?.items)));setTaskPage(next?.pagination?.page||taskPage+1);setTaskPagination(next?.pagination||taskPagination)}
    catch(error){if(error.name!=='AbortError'&&generation===read.generation)setTaskListError(error.message)}finally{if(generation===read.generation&&!controller.signal.aborted)setLoadingMoreTasks(false)}
  };
  const [editTask,setEditTask]=useState(()=>preEditId?tasks.find(t=>t?.id===preEditId)||null:null);
  const account=accounts.find(a=>a?.id===accountId);
  const taskLimit=membershipMaxTasks(membership);
  const enabledTaskCount=Number.isFinite(Number(membership?.enabled_task_count))
    ? Number(membership.enabled_task_count)
    : Number(data?.resource_limits?.tasks_enabled_total ?? tasks.filter(task=>task?.enabled).length);
  const remainingTaskCapacity=taskLimit == null || taskLimit >= 2147483647 ? Infinity : Math.max(0, taskLimit-enabledTaskCount);
  const freeTier=membershipIsFreeTier(membership);
  const quotaMessage=freeTier
    ? '免费版可创建 1 个好友任务，升级订阅可继续添加。'
    : '当前套餐的好友任务额度已用完，请升级或续费后继续添加。';
  const shown=tasks.filter(t=>(!contextAccount||t.accountId===contextAccount.id)&&(filter==='all'||(filter==='active'?t?.enabled:!t?.enabled))&&(!search||displayName(t?.name,'').toLowerCase().includes(search.toLowerCase())));
  const closeEdit=()=>{setEditTask(null);onEditClose?.()};
  const toggleTask=async task=>{
    if(!task?.id)return;
    setOpenTaskMenu(null);
    await rowFeedback.run(task.id,
      ()=>api(`/api/studio/tasks/${task.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':data?.csrf},body:JSON.stringify({enabled:!task.enabled})}),
      ()=>{setTaskRows(rows=>rows.map(item=>item.id===task.id?{...item,enabled:!task.enabled}:item));void onRefresh?.();},
      task.enabled?'任务已暂停':'任务已恢复');
  };
  const deleteTask=async task=>{
    if(!task?.id)return;
    const name=displayName(task.name,'未命名好友');
    if(!await confirmModal({title:'删除续火任务',eyebrow:'TASK WORKBENCH',message:`确定删除发给 ${name} 的任务吗？删除后可重新选择该好友。`,confirmLabel:'确认删除'}))return;
    try {
      setOpenTaskMenu(null);
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
    if(remainingTaskCapacity===0||friends.length>remainingTaskCapacity)return setNotice(quotaMessage);
    if(!account)return setNotice('请选择发送账号');
    if(kind==='text'&&!message.trim())return setNotice('请填写发送内容');
    setNotice('');
    await composer.create({csrf:data.csrf,payload:{account_id:account.id,targets:friends.map(f=>({name:f.name,sec_uid:f.sec_uid||''})),message_kind:kind,message_template:kind==='text'?message.trim():'续火花'}});
  };
  const enabledTotal=data?.resource_limits?.tasks_enabled_total!=null?data.resource_limits.tasks_enabled_total+createdPending:tasks.filter(t=>t?.enabled).length;
  const pausedTotal=data?.resource_limits?.tasks_paused_total??Math.max(0,(taskPagination?.total??tasks.length)-enabledTotal);
  const contextTasks=contextAccount?tasks.filter(task=>task.accountId===contextAccount.id):tasks;
  const visibleTaskTotal=taskPagination?.total??(contextAccount?(data.resource_limits?.task_counts_by_account?.[contextAccount.id]??contextTasks.length):tasks.length);
  const visibleEnabled=contextAccount?contextTasks.filter(task=>task.enabled).length:enabledTotal;
  const visiblePaused=contextAccount?contextTasks.filter(task=>!task.enabled).length:pausedTotal;
  const firstMinutes=time=>{const [hour,minute]=time.split(':').map(Number);return hour*60+minute};
  const ready=schedule.status==='ready';
  const scheduledFriends=ready?schedule.plan.targets||friends:friends;
  const closeSuccessToast=React.useCallback(()=>setSuccessToast(''),[]);
  return <>
    <PageHeader eyebrow="02 / ROUTINE STUDIO" title="续火任务" text="管理好友、发送时间与每日任务。" actions={<Button onClick={()=>document.getElementById('task-composer')?.scrollIntoView({behavior:'smooth'})}>新建任务</Button>}/>
    {contextAccount&&<section className="task-context" aria-label="当前账号任务"><span className="avatar" style={{viewTransitionName:'account-avatar'}}>{contextAccount.profile?.avatar_url?<img src={contextAccount.profile.avatar_url} alt=""/>:initial(contextAccount.name)}</span><div><b style={{viewTransitionName:'account-title'}}>{displayName(contextAccount.name)}</b><small>正在查看此账号的续火任务</small></div><Button variant="quiet" onClick={()=>contextNavigate('/studio')}>查看全部任务</Button></section>}
    {schedule.success&&<section className="task-create-success" ref={successRef} tabIndex={-1} role="status" aria-live="polite">
      <div className="task-create-success-head"><span className="task-created-mark" aria-hidden="true">✓</span><div><h2>已创建 {schedule.success.count} 个续火任务</h2><p>每日计划已保存，将按以下时间发送。所有时间均为北京时间。</p></div></div>
      <ul className="task-created-list">{schedule.success.items.map((task,index)=><li key={task.id}><b>{displayName(task.name,'未命名好友')}</b><span>每天 <time>{task.time}</time></span><small>首次：{scheduleFirstRunLabel({...schedule.success.entries[index],time:task.time})}</small></li>)}</ul>
      {contextAccount&&schedule.success.items.some(task=>task.accountId!==contextAccount.id)&&<Button variant="quiet" onClick={()=>contextNavigate(`/studio?account=${encodeURIComponent(schedule.success.items[0].accountId)}`)}>查看新建任务</Button>}
      {schedule.success.refreshError&&<p className="task-success-refresh">任务已保存，页面数据暂未刷新。<Button variant="quiet" onClick={()=>refreshRef.current?.()}>刷新列表</Button></p>}
    </section>}
    <div className="studio-layout"><section className="studio-list">
      <div className="toolbar"><RubberSegment className="admin-segment" items={[{value:'all',label:`全部 ${visibleTaskTotal}`},{value:'active',label:`${contextAccount&&taskPagination?.has_next?'已载入启用':'已启用'} ${visibleEnabled}`},{value:'paused',label:`${contextAccount&&taskPagination?.has_next?'已载入暂停':'已暂停'} ${visiblePaused}`}]} value={filter} onChange={setFilter} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="md" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="任务筛选"/><input className="input compact-search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索好友或备注"/></div>
      <FilterSummary items={[{key:'search',label:'好友',value:search,onRemove:()=>setSearch('')},{key:'state',label:'状态',value:filter==='all'?'':filter==='active'?'已启用':'已暂停',onRemove:()=>setFilter('all')}]} onClear={()=>{setSearch('');setFilter('all')}}/>
      {contextLoading&&shown.length>0&&<ReadStatus busy/>}
      {taskListError&&<div className="inline-feedback danger-text" role="alert">{taskListError}<Button variant="quiet" onClick={onRefresh}>重新读取</Button></div>}
      {contextLoading&&!shown.length?<ListSkeleton label="正在读取此账号的任务…"/>:taskListError&&!shown.length?null:shown.length?<div className="task-table"><div className="table-head"><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div><MotionList className="task-motion-list">{shown.map((task,index)=>{const name=displayName(task?.name,'未命名好友');const actionClass='icon-action';const menuOpen=openTaskMenu===task?.id;return <div id={`task-row-${task.id}`} className="task-row" data-feedback={rowFeedback.states[task.id]?.kind} aria-busy={rowFeedback.states[task.id]?.kind==='pending'||undefined} data-motion-state={task?.enabled?'enabled':'paused'} key={task?.id||`${name}-${index}`}><span className="person-cell"><motion.span layoutId={reduced?undefined:`task-avatar-${task.id}`} className="avatar" transition={{duration:.32,ease:[.22,1,.36,1]}}>{task?.avatar?<img src={task.avatar} alt=""/>:initial(name)}</motion.span><span><motion.b layoutId={reduced?undefined:`task-name-${task.id}`} transition={{duration:.32,ease:[.22,1,.36,1]}}>{name}</motion.b><small>{displayText(task?.kind,'续火花表情')}</small></span></span><time>{displayText(task?.time,'—')}<small>每天</small></time><span className="task-status"><Badge tone={task?.enabled?'good':'neutral'}>{task?.enabled?'已启用':'已暂停'}</Badge><small>{friendState(task,asList(data.runs).find(run=>run.taskId===task.id)).label}</small></span><span className="row-actions"><Button className={actionClass} variant="quiet" title="编辑任务" aria-label={`编辑 ${name}`} disabled={rowFeedback.states[task.id]?.kind==='pending'} onClick={()=>{setOpenTaskMenu(null);setEditTask(task)}}><ActionEditIcon/></Button><Button className={actionClass} variant="quiet" title={task?.enabled?'暂停任务':'恢复任务'} aria-label={`${task?.enabled?'暂停':'恢复'} ${name}`} onClick={()=>toggleTask(task)}>{task?.enabled?<ActionPauseIcon/>:<ActionPlayIcon/>}</Button><div className="task-row-menu"><button type="button" className={`task-row-menu-btn${menuOpen?' is-open':''}`} aria-expanded={menuOpen} aria-label={`${name} 更多操作`} onClick={e=>{e.stopPropagation();setOpenTaskMenu(menuOpen?null:task?.id)}}><StatusIcon kind="more"/></button><div className={`task-row-menu-dropdown${menuOpen?' is-open':''}`}>{menuOpen&&<TaskProbeAction taskId={task.id} csrf={data.csrf} label="运行前自检"/>}<button type="button" className="danger-action" onClick={()=>deleteTask(task)}>删除任务</button></div></div></span><RowFeedback value={rowFeedback.states[task.id]}/></div>})}</MotionList></div>:<Empty title={search||filter!=='all'?'没有匹配的任务':'还没有续火任务'} text={search||filter!=='all'?'调整搜索条件，或清除筛选查看全部任务。':'先选择账号和好友，创建第一条每日计划。'} action={search||filter!=='all'?<Button variant="quiet" onClick={()=>{setSearch('');setFilter('all')}}>清除筛选</Button>:null}/>}
      {taskPagination?.has_next&&<div className="setting-actions"><Button variant="quiet" disabled={loadingMoreTasks} onClick={loadMoreTasks}>{loadingMoreTasks?'正在加载…':`加载更多任务（已显示 ${tasks.length} / ${taskPagination.total}）`}</Button></div>}
    </section><Card className="composer-card" id="task-composer">
      <div className="section-head"><div><span className="eyebrow">NEW CONNECTION</span><h2>新建续火任务</h2></div></div>
      <div className="composer-account-bar">
        <span className="eyebrow">发送账号</span>
        <SiteSelect label="发送账号" value={accountId} disabled={saving||!accounts.length} onValueChange={value=>{setAccountId(value);setFriends([]);setSelectedTime('')}} items={accounts.length?accounts.map(item=>({value:item.id,label:displayName(item.name)})):[{value:'',label:'请先绑定账号'}]}/>
        {!accounts.length&&<Link to="/accounts" className="composer-account-link text-action">前往绑定<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link>}
      </div>
      <form onSubmit={async e=>{e.preventDefault();await createTask()}} className="composer-single-form">
        <div className={`composer-main-area${friends.length&&ready?' has-preview':''}`}>
          <div className="composer-friend-section">
            <label className="composer-field-label">目标好友</label>
            <div className={`friend-trigger ${remainingTaskCapacity===0?'is-quota-full':''}`} onClick={()=>account&&setPicker(true)}>
              {friends.length?friends.map((friend,index)=><span key={friend?.sec_uid||friend?.name||index} className="selected-chip">{displayName(friend?.name,'未命名好友')}</span>):<span className="muted">点击选择好友</span>}
              <span>＋</span>
            </div>
            {remainingTaskCapacity===0&&<small className="composer-quota-hint">{quotaMessage}</small>}
          </div>
          {friends.length>0&&(schedule.status==='loading'||ready)&&(
            <div className="composer-preview-section" aria-label="发送时间预览" aria-busy={schedule.status==='loading'}>
              <label className="composer-field-label">发送时间安排</label>
              {schedule.status==='loading'&&<p className="composer-preview-loading" role="status">正在计算时间…</p>}
              {schedule.error&&<div className="inline-feedback danger-text" role="alert">{schedule.error}<Button variant="quiet" type="button" disabled={saving} onClick={()=>composer.retry()}>重试</Button></div>}
              {schedule.conflict&&<div className="inline-feedback" role="alert"><b>时间有冲突，请确认</b><p>{schedule.conflict}</p></div>}
              {ready&&<ul className="composer-preview-list">{scheduledFriends.map((friend,index)=>{const entry=schedule.plan.entries[index];const offset=(firstMinutes(entry.time)-firstMinutes(schedule.plan.times[0])+1440)%1440;return <li key={friend.sec_uid||friend.name||index}><span>{displayName(friend.name,'未命名好友')}</span><time>{entry.time}</time>{index>0&&<small>+{offset}分</small>}</li>})}</ul>}
            </div>
          )}
        </div>
        <div className="composer-settings-row">
          <label className="composer-field-inline"><span>时间</span><input className="input" type="time" aria-label="每日发送时间" value={selectedTime||schedule.plan?.times?.[0]||''} disabled={saving||schedule.status==='loading'} onChange={event=>setSelectedTime(event.target.value)}/></label>
          <label className="composer-field-inline"><span>类型</span><Select className="input" value={kind} disabled={saving} onChange={event=>setKind(event.target.value)} aria-label="消息类型"><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></Select></label>
        </div>
        {kind==='text'&&<label className="composer-text-label">发送内容<textarea className="input textarea" maxLength="300" disabled={saving} value={message} onChange={event=>setMessage(event.target.value)} placeholder="写一句日常问候"/></label>}
        {notice&&<div className="inline-feedback" role="status">{notice}</div>}
        {schedule.createError&&<div className="inline-feedback danger-text" role="alert"><b>未创建任务</b><p>{schedule.createError}</p></div>}
        <div className="composer-actions">
          <Button type="submit" busy={saving} busyLabel="正在创建" disabled={!friends.length||!account||!membership.can_add_task||remainingTaskCapacity===0||!ready}>{friends.length?`创建 ${scheduledFriends.length} 个续火任务`:'选择好友后创建'}</Button>
          {!membership.can_add_task&&<small className="muted">{quotaMessage}</small>}
        </div>
      </form>
    </Card></div>
    <ContactPicker open={picker} onClose={()=>setPicker(false)} account={account} data={data} releasedKeys={releasedKeys} selectionLimit={Number.isFinite(remainingTaskCapacity)?remainingTaskCapacity:null} limitMessage={remainingTaskCapacity===0?quotaMessage:''} onLimitReached={()=>setNotice(quotaMessage)} onDone={value=>{if(value)setFriends(value)}}/><TaskEditDrawer task={editTask} data={data} open={Boolean(editTask)} onClose={closeEdit} onRefresh={onRefresh} onSaved={(msg,id,patch)=>{setTaskRows(rows=>rows.map(row=>row.id===id?{...row,...patch}:row));rowFeedback.mark(id,msg);setSuccessToast(msg);flashTask(id)}}/><Toast message={successToast} onClose={closeSuccessToast}/>
  </>;
}

function Accounts({data,onRefresh}) {
  const accountNavigate=useAccountNavigation();
  const [historyAccount,setHistoryAccount]=useState(null);
  const [accountChecks,setAccountChecks]=useState({});
  const checkLocks=useRef(new Set());
  const [notice,setNotice]=useState('');
  const [scan,setScan]=useState(null); const [error,setError]=useState(''); const [busy,setBusy]=useState(false); const [frameReady,setFrameReady]=useState(false); const navigate=useNavigate();
  const lifecycle=useRef({mounted:true,scan:null,starting:false,cancelling:false});
  lifecycle.current.scan=scan;
  const cancelRemote=async current=>{if(!current?.id||['succeeded','failed','expired','cancelled'].includes(current.status))return;const fd=new FormData();fd.append('csrf_token',data?.csrf||'');await api(`/accounts/scan/${current.id}/cancel`,{method:'POST',body:fd,keepalive:true})};
  useEffect(()=>{lifecycle.current.mounted=true;return()=>{lifecycle.current.mounted=false;cancelRemote(lifecycle.current.scan).catch(()=>{})}},[]);
  const closeScan=async()=>{if(lifecycle.current.cancelling)return;lifecycle.current.cancelling=true;try{await cancelRemote(scan);setScan(null);setError('')}catch(error){setError(`未能结束云端会话，请重试：${error.message}`)}finally{lifecycle.current.cancelling=false}};
  const accounts=Array.isArray(data?.accounts)?data.accounts:[];
  const tasks=Array.isArray(data?.tasks)?data.tasks:[];
  useEffect(()=>{setAccountChecks(current=>Object.fromEntries(Object.entries(current).map(([id,request])=>[id,accountCheckResult(accounts.find(account=>account.id===id),request)])))},[data.accounts]);
  const hasPendingCheck=Object.values(accountChecks).some(check=>check.state==='pending');
  useEffect(()=>{
    if(!hasPendingCheck)return;
    const timer=setInterval(()=>{
      setAccountChecks(current=>Object.fromEntries(Object.entries(current).map(([id,request])=>[id,accountCheckResult(accounts.find(account=>account.id===id),request)])));
      if(document.visibilityState!=='hidden')void onRefresh?.();
    },5000);
    return()=>clearInterval(timer);
  },[hasPendingCheck,onRefresh,accounts]);
  const blockedAccounts=accounts.filter(accountNeedsRecovery);
  const affectedTaskCount=blockedAccounts.reduce((sum,account)=>sum+(data.resource_limits?.task_counts_by_account?.[account.id]??tasks.filter(task=>task.accountId===account.id).length),0);
  const membership=data?.membership||{};
  const canAddAccount=membership.is_admin===true || membership.can_add_account!==false;
  const freeTier=membershipIsFreeTier(membership);
  const refreshScan=async()=>{if(!scan?.id)return;for(let attempt=0;attempt<8;attempt++){await new Promise(resolve=>setTimeout(resolve,attempt?350:120));try{const next=await api(`/accounts/scan/${scan.id}`);if(next&&typeof next==='object')setScan(current=>current?.id===scan.id?next:current)}catch(_error){break}}};
  useEffect(()=>{if(!scan?.id)return;let timer;let failures=0;let cancelled=false;const poll=async()=>{if(document.visibilityState==='hidden'){timer=setTimeout(poll,5000);return}try{const next=await api(`/accounts/scan/${scan.id}`);if(cancelled)return;if(failures)setError('');failures=0;if(!next||typeof next!=='object'){setError('登录状态暂时没有返回，请重新绑定');setScan(null);return}setScan(next);if(!['succeeded','failed','expired','cancelled'].includes(next.status))timer=setTimeout(poll,1500);else if(next.status==='succeeded')onRefresh()}catch(e){if(cancelled)return;failures+=1;setError(failures>=8?`暂时无法读取扫码状态，正在继续重试：${e.message}`:`连接短暂中断，${Math.min(failures*2,10)} 秒后自动重试`);timer=setTimeout(poll,Math.min(failures*2000,10000))}};timer=setTimeout(poll,500);return()=>{cancelled=true;clearTimeout(timer)}},[scan?.id,onRefresh]);
  const start=async(accountId='')=>{if(lifecycle.current.starting)return;if(!accountId&&!canAddAccount){setError(freeTier?'免费版已绑定 1 个抖音账号，升级订阅可绑定更多账号。':'当前套餐的抖音账号额度已用完，请升级或续费后继续绑定。');return}lifecycle.current.starting=true;setBusy(true);setError('');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');if(accountId)fd.append('account_id',accountId);const v=await api(accountId?'/accounts/recovery':'/accounts/scan',{method:'POST',body:fd});if(!v||typeof v!=='object')throw new Error(accountId?'复核服务没有返回会话状态':'绑定服务没有返回登录状态');if(!lifecycle.current.mounted){await cancelRemote(v);return}setScan(v)}catch(e){if(lifecycle.current.mounted)setError(e.message)}finally{lifecycle.current.starting=false;if(lifecycle.current.mounted)setBusy(false)}};
  const terminal=scan&&['failed','expired','cancelled'].includes(scan.status); const recovery=scan?.mode==='recovery';
  const requestHealthCheck=async accountId=>{
    if(checkLocks.current.has(accountId)||['submitting','pending'].includes(accountChecks[accountId]?.state))return;
    checkLocks.current.add(accountId);
    const startedAt=Date.now();
    setAccountChecks(current=>({...current,[accountId]:{state:'submitting',message:'正在提交检查…'}}));
    const fd=new FormData();fd.append('csrf_token',data?.csrf||'');
    try{
      await api(`/accounts/${accountId}/health-check`,{method:'POST',body:fd});
      setAccountChecks(current=>({...current,[accountId]:{state:'pending',message:'已提交检查，正在等待账号结果…',previousChecked:accounts.find(account=>account.id===accountId)?.health?.checked_at,startedAt}}));
      void onRefresh?.();
    }catch(error){setAccountChecks(current=>({...current,[accountId]:{state:'error',message:`检查未提交：${error.message}`}}))}
    finally{checkLocks.current.delete(accountId)}
  };
  const openAccountCheck=account=>{const health=account?.health||{};const blocked=health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(account?.validation_state);if(blocked)start(account.id);else requestHealthCheck(account.id)};
  const recoveryError=error&&(/未找到抖音账号|账号不存在|404/.test(error));
  useEffect(()=>{if(recoveryError)setError('账号复核失败：原账号可能已删除或权限已变化，请重新绑定。')},[recoveryError]);
  useEffect(()=>{setFrameReady(false);document.body.dataset.dyfireFrameReady='false'},[scan?.id]);
  useEffect(()=>{const ready=()=>setFrameReady(true);document.addEventListener('dyfire-frame-ready',ready);return()=>document.removeEventListener('dyfire-frame-ready',ready)},[]);
  return <><PageHeader eyebrow="03 / DOUYIN ACCOUNTS" title="抖音账号" text="查看登录状态，处理账号验证。" actions={<Button onClick={()=>canAddAccount?start():navigate('/subscription')} disabled={busy}>{busy?'正在准备…':canAddAccount?'绑定抖音账号':'查看订阅额度'}</Button>}/>{error&&<div className="inline-feedback danger-text">{error}{!canAddAccount&&<> <Link to="/subscription" className="text-action">查看套餐<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></>}</div>}<Toast message={notice} onClose={()=>setNotice('')}/>{blockedAccounts.length>0&&<div className="account-attention-summary" role="status"><strong>{blockedAccounts.length} 个账号需要处理</strong><span>关联 {affectedTaskCount} 个任务 · 查看下方状态并按提示处理</span></div>}<div className="account-grid">{accounts.length?[...accounts].sort((a,b)=>Number(b.health?.blocked||false)-Number(a.health?.blocked||false)).map(a=>{const name=String(a?.name||'抖音账号'); const profile=a?.profile||{}; const health=a?.health||{}; const taskCount=data?.resource_limits?.task_counts_by_account?.[a.id]??tasks.filter(t=>t?.accountId===a?.id).length; return <Card className="account-card" id={`account-${a.id||name}`} key={a.id||name}><div className="account-card-head"><span className="avatar avatar-large">{profile.avatar_url?<img src={profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><h2>{name}</h2><small>{profile.nickname||'抖音账号'}</small></div><Badge tone={(health.state==='valid'||a.validation_state==='valid')?'good':'warn'}>{health.label||'等待复核'}</Badge></div>{health.reason&&<p className="account-health-note">{health.reason}</p>}{a.incident&&<p className="account-health-note">本次异常影响 {a.incident.affected_tasks} 个任务 · {a.incident.recovery_actions} 次复核</p>}<div className="account-card-meta"><span>最近检查<strong>{health.checked_at?new Date(health.checked_at).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'等待首次检查'}</strong></span><span>续火任务<strong>{taskCount} 条</strong></span></div><div className="account-card-actions"><Button variant="quiet" onClick={event=>accountNavigate(a,event.currentTarget.closest('.account-card'))}>查看任务</Button><Button variant="quiet" disabled={health.checking||['submitting','pending'].includes(accountChecks[a.id]?.state)} onClick={()=>{const blocked=health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(a.validation_state);return blocked?start(a.id):requestHealthCheck(a.id)}}>{health.checking||['submitting','pending'].includes(accountChecks[a.id]?.state)?'正在复核…':(health.blocked||['verification','invalid','relogin'].includes(health.state)||['verification','invalid'].includes(a.validation_state))?'打开验证':'复核状态'}</Button></div>{accountChecks[a.id]&&<div className="account-check-feedback" role={accountChecks[a.id].state==='error'?'alert':'status'} data-state={accountChecks[a.id].state}>{accountChecks[a.id].message}{accountChecks[a.id].state==='waiting'&&<Button variant="quiet" onClick={onRefresh}>刷新结果</Button>}</div>}<div className="account-secondary"><button type="button" className="support-inline-link" onClick={()=>setHistoryAccount(a)}>查看异常记录</button><AccountLifecycle account={a} csrf={data.csrf} api={api} confirmModal={confirmModal} onRefresh={onRefresh}/></div></Card>}):<Empty title="还没有绑定抖音账号" text={freeTier?'免费版可直接绑定 1 个抖音账号，扫码后按抖音 App 提示确认登录。':'扫码后按抖音 App 提示确认登录。'} action={<Button onClick={()=>start()}>开始绑定</Button>}/>}</div><AnimatedDetails className="account-guide"><summary>账号验证与续火说明</summary><p>系统会在发送前复核账号状态；遇到抖音安全验证时，会暂停相关计划并在这里显示处理入口。</p></AnimatedDetails>{historyAccount&&<React.Suspense fallback={<LoadingState/>}><AccountIncidents account={historyAccount} onClose={()=>setHistoryAccount(null)} api={api} Drawer={Drawer} Badge={Badge} LoadingState={LoadingState}/></React.Suspense>}<Drawer open={!!scan} onClose={closeScan} title={recovery?'复核原抖音账号':'绑定抖音账号'} eyebrow={recovery?'ACCOUNT RECOVERY':'SECURE LOGIN'} wide centered><div className="scan-panel">{error&&<p className="inline-feedback danger-text" role="alert">{error}</p>}{scan?.status==='succeeded'?<><div className="success-mark">✓</div><h3>{recovery?'账号已恢复':'绑定成功'}</h3><p>{recovery?'账号状态已确认正常，已保留该账号、好友和续火任务，后续计划会继续执行。':'抖音账号已安全保存，可以创建续火任务。'}</p><Button onClick={()=>{setScan(null);recovery?onRefresh():navigate('/studio')}}>{recovery?'返回账号':'去创建任务'}</Button></>:terminal?<><div className="success-mark failure-mark">!</div><h3>{recovery?'本次复核未完成':'本次绑定未完成'}</h3><p>{scan?.message|| (recovery?'原账号登录状态仍需处理，请重新打开验证页面。':'二维码已失效，请重新发起绑定。')}</p><Button onClick={()=>start(recovery?scan?.account_id||'':'')}>{recovery?'继续复核':'重新绑定'}</Button></>:<><BindingProgress scan={scan}/><CloudFrame scan={scan} data={data} onError={setError} onScanChange={setScan} onInteraction={refreshScan}/><AnimatedDetails className="binding-help"><summary>正在用同一部手机？</summary><p className="same-device-scan-help">将二维码显示在另一台设备，再用此手机的抖音 App 扫码。抖音页面提供手机号或密码入口时，也可点击画面选择该方式。</p></AnimatedDetails><h3>{scan?.message||(recovery?'正在恢复原账号的云端验证页面':'请使用抖音 App 扫码并在手机确认')}</h3><p>{recovery?'系统正在检查原账号登录状态。页面没有验证提示时会自动完成复核，无需输入；只有抖音明确要求验证或重新登录时，才需要按画面提示操作。':scan?.needs_verification?'抖音已要求额外验证，请在下方完成一次提交。':'二维码只在当前登录窗口有效，完成确认后页面会自动更新状态。'}</p><CloudInteraction scan={scan} data={data} onInteraction={refreshScan}/><ScanVerification scan={scan} data={data} setScan={setScan}/><Button variant="quiet" onClick={closeScan}>{recovery?'结束本次复核':'取消绑定'}</Button></>}</div></Drawer></>;
}

function Runs({data}) {
  const location=useLocation();
  const requestedFilter=new URLSearchParams(location.search).get('status')||'';
  const initialFilter=['success','failed','submitted_unconfirmed','running'].includes(requestedFilter)?requestedFilter:'';
  const [rows,setRows]=useState(asList(data?.runs));
  const navigate=useNavigate();
  const [filter,setFilterState]=useState(initialFilter);
  useEffect(()=>setFilterState(initialFilter),[initialFilter]);
  const setFilter=value=>{setFilterState(value);setRunPage(1);const params=new URLSearchParams(location.search);if(value)params.set('status',value);else params.delete('status');navigate({pathname:'/runs',search:params.toString()},{replace:true})};
  const [expanded,setExpanded]=useState(null);
  const [runsError,setRunsError]=useState('');
  const nameFilter=new URLSearchParams(location.search).get('q')||'';
  const setNameFilter=value=>{setRunPage(1);const params=new URLSearchParams(location.search);if(value)params.set('q',value);else params.delete('q');navigate({pathname:'/runs',search:params.toString()},{replace:true})};
  const [runPage,setRunPage]=useState(1), [runPagination,setRunPagination]=useState(null), [reading,setReading]=useState(true);
  const runQuery=useDebounced(nameFilter);
  useEffect(()=>setExpanded(null),[filter,runQuery,runPage]);
  useEffect(()=>{
    const controller=new AbortController();setReading(true);setRunsError('');
    const params=new URLSearchParams({status:filter,page:String(runPage),q:runQuery});
    api(`/api/studio/runs?${params}`,{signal:controller.signal}).then(value=>{
      if(controller.signal.aborted)return;
      setRows(asList(value.items));setExpanded(current=>value.items?.some(item=>item.id===current)?current:null);setRunPagination({total:value.total??value.items?.length??0,page:value.page??runPage,pages:value.pages??1});
    }).catch(error=>{if(error.name!=='AbortError')setRunsError(error.message)}).finally(()=>{if(!controller.signal.aborted)setReading(false)});
    return()=>controller.abort();
  },[filter,runQuery,runPage,data?.runs]);
  const displayedRows=rows;
  const tone=r=>r.status==='success'?'good':r.status==='failed'?'danger':r.status==='submitted_unconfirmed'?'warn':r.status==='running'?'running':'neutral';
  const runFilterItems=[{value:'',label:'全部'},{value:'success',label:'已发出'},{value:'failed',label:'未发出'},{value:'running',label:'执行中'},{value:'submitted_unconfirmed',label:'待核实'}];
  return <><PageHeader eyebrow="04 / DELIVERY LOG" title="执行记录" text="每次发送都保留可核对的状态、阶段和尝试历史。"/><div className="runs-summary" aria-label="当前页执行统计"><span className="muted-copy">当前页</span><span className="runs-summary-item good"><b>{rows.filter(r=>r.status==='success').length}</b> 已发出</span><span className="runs-summary-item warn"><b>{rows.filter(r=>r.status==='submitted_unconfirmed').length}</b> 待核实</span><span className="runs-summary-item danger"><b>{rows.filter(r=>r.status==='failed').length}</b> 未发出</span></div><div className="toolbar runs-toolbar"><RubberSegment className="admin-segment" items={runFilterItems} value={filter} onChange={setFilter} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="md" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="执行记录筛选"/><input className="input compact-search" value={nameFilter} onChange={e=>{setNameFilter(e.target.value);setRunPage(1)}} placeholder="按好友名搜索"/><span className="runs-count">{reading?'读取中…':`${runPagination?.total??rows.length} 条记录`}</span></div><FilterSummary items={[{key:'q',label:'好友',value:nameFilter,onRemove:()=>setNameFilter('')},{key:'status',label:'结果',value:filter?runFilterItems.find(item=>item.value===filter)?.label:'',onRemove:()=>setFilter('')}]} onClear={()=>{setFilterState('');setRunPage(1);navigate('/runs',{replace:true})}}/>{runsError&&<div className="inline-feedback danger-text" role="alert">{runsError}</div>}<Card className="runs-card" aria-busy={reading}>{reading&&!rows.length?<ListSkeleton label="正在读取执行记录…"/>:displayedRows.length?<div className="run-list"><MotionList className="run-motion-list">{displayedRows.map(r=>{const open=expanded===r.id;const detailId=`run-detail-${r.id}`;return <div className={`run-row ${open?'is-expanded':''}`} data-motion-state={r.status||'unknown'} key={r.id} role="button" tabIndex="0" aria-expanded={open} aria-controls={detailId} aria-label={`${r.name||'执行记录'}，${r.label||r.status||'状态未知'}。按 Enter 或空格查看详情`} onClick={event=>{if(event.target.closest('button,a,input,select'))return;setExpanded(open?null:r.id)}} onKeyDown={e=>{if(e.target.closest?.('button,a,input,textarea,select'))return;if(e.key==='Enter'||e.key===' '){e.preventDefault();setExpanded(open?null:r.id)}}}><span className={`run-dot ${r.status}`} aria-hidden="true"/><div className="grow"><b>{r.name}</b><small>{r.time} · {r.kind||r.stage}</small><MotionDisclosure open={open} id={detailId} className="run-detail"><span>执行阶段：{r.stage||'—'}</span><span>{r.error||r.guidance?.text||(r.status==='success'?'本次执行已确认完成。':'系统仍在处理，请等待结果。')}</span>{['account_verification_required','account_login_required','login_expired'].includes(r.error_code)&&data.tasks?.find(task=>task.id===r.taskId)?.accountId&&<Link className="text-action run-recovery-link" to={`/accounts#account-${data.tasks.find(task=>task.id===r.taskId).accountId}`}>查看账号并处理验证<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link>}{r.support_id&&<SupportReference value={r.support_id}/>} {open&&r.status==='submitted_unconfirmed'&&r.taskId&&<TaskProbeAction taskId={r.taskId} runId={r.id} csrf={data?.csrf} label="核对这次发送（只读）"/>}{r.timeline?.map(item=><span key={`timeline-${item.sequence}`}><b>{item.occurred_at} · {item.stage}</b> {item.detail||item.event}</span>)}{r.attempts?.map(a=><span key={a.number}>第 {a.number} 次：{a.reason||a.stage||'—'}</span>)}</MotionDisclosure></div><Badge tone={tone(r)}>{r.label||r.status}</Badge><span className="run-error">{r.error||r.guidance?.text||(r.status==='success'?'已确认发送完成':r.status==='submitted_unconfirmed'?'发送结果待核实':'等待执行结果')}</span></div>})}</MotionList></div>:<Empty title={filter||nameFilter?'没有匹配的执行记录':'还没有执行记录'} text={filter||nameFilter?'试试其他结果类型，或清除搜索条件。':'任务开始运行后，结果会出现在这里。'} action={filter||nameFilter?<Button variant="quiet" onClick={()=>{setFilterState('');setRunPage(1);navigate('/runs',{replace:true})}}>清除筛选</Button>:null}/>}</Card>{runPagination&&runPagination.pages>1&&<div className="admin-pagination"><span>共 {runPagination.total} 条 · 第 {runPagination.page} / {runPagination.pages} 页</span><div><Button variant="quiet" disabled={reading||runPagination.page<=1} onClick={()=>setRunPage(runPagination.page-1)}>上一页</Button><Button variant="quiet" disabled={reading||runPagination.page>=runPagination.pages} onClick={()=>setRunPage(runPagination.page+1)}>下一页</Button></div></div>}</>;
}

function TaskEditDrawer({task, data, open, onClose, onRefresh, onSaved}) {
  const reduced=useReducedMotion();
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
    if(saving)return;
    setSaving(true); setStatus('正在保存…');
    const fd=new FormData();
    fd.append('csrf_token',data?.csrf||'');
    fd.append('account_id',t?.accountId||'');
    fd.append('target_name',t?.name||'');
    fd.append('target_sec_uid',t?.sec_uid||'');
    fd.append('send_time',time);
    fd.append('message_kind',kind);
    fd.append('message_template',kind==='text'?message:'续火花');
    try{
      await api(`/tasks/${t?.id}/edit`,{method:'POST',body:fd});
      onSaved?.('任务已保存',t.id,{time,kind:kind==='text'?'文字消息':'续火花表情',message:kind==='text'?message:'续火花'});
      onClose();
      void onRefresh?.();
    }catch(err){setStatus(err.message);}
    finally{setSaving(false);}
  };
  const taskRef=React.useRef(task);
  if(task)taskRef.current=task;
  const t=taskRef.current;
  const name=displayName(t?.name,'未命名好友');
  const dirty=!!t&&(time!==(t.time||'09:30')||kind!==(t.kind==='文字消息'?'text':'douyin_sticker')||(kind==='text'&&message!==(t.message||'')));
  useEffect(()=>{
    if(!open||!dirty)return;
    const prevent=event=>{event.preventDefault();event.returnValue=''};
    window.addEventListener('beforeunload',prevent);
    return()=>window.removeEventListener('beforeunload',prevent);
  },[open,dirty]);
  const closeEditor=async()=>{
    if(saving)return false;
    if(dirty)return confirmModal({title:'放弃未保存的修改？',message:'发送时间或消息内容还未保存。',confirmLabel:'放弃修改',cancelLabel:'继续编辑'});
    return true;
  };

  return (
    <Drawer open={open} onClose={onClose} canClose={closeEditor} title={open?`编辑 ${name}`:' '} eyebrow="TASK SETTINGS" centered size="compact">
      <div className="drawer-content">
        <form className="form-stack" onSubmit={save}>
          <div className="edit-target">
            <motion.span layoutId={!reduced&&open?`task-avatar-${t?.id}`:undefined} className="avatar avatar-large" transition={{duration:.32,ease:[.22,1,.36,1]}}>{t?.avatar?<img src={t.avatar} alt=""/>:initial(t?.name,'未')}</motion.span>
            <div><motion.b layoutId={!reduced&&open?`task-name-${t?.id}`:undefined} transition={{duration:.32,ease:[.22,1,.36,1]}}>{name}</motion.b><small>目标好友 · 账号任务</small></div>
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
          {dirty&&<small className="muted">更改将在保存后用于后续计划。</small>}{status&&<div className={saving?'inline-feedback':'inline-feedback danger-text'} role={saving?'status':'alert'}>{status}</div>}
          <Button type="submit" busy={saving} busyLabel="正在保存">保存任务</Button>
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
  const [state,setState]=useState(null); const [status,setStatus]=useState(''); const [email,setEmail]=useState(''); const [enabled,setEnabled]=useState(false); const [events,setEvents]=useState([]); const [quietEnabled,setQuietEnabled]=useState(false); const [quietStart,setQuietStart]=useState(23); const [quietEnd,setQuietEnd]=useState(8); const [notifyInterval,setNotifyInterval]=useState(60); const [code,setCode]=useState(''); const [busy,setBusy]=useState(false);
  const load=async()=>{const v=await api('/api/notifications');setState(v||{});setEmail(v?.preference?.email||'');setEnabled(!!v?.preference?.enabled);setEvents(asList(v?.policy?.events));setQuietEnabled(!!v?.policy?.quiet_enabled);setQuietStart(Number(v?.policy?.quiet_start??23));setQuietEnd(Number(v?.policy?.quiet_end??8));setNotifyInterval(Number(v?.policy?.interval_minutes??60));return v};
  useEffect(()=>{load().catch(e=>setStatus(e.message))},[]);
  const csrf=()=>state?.csrf||data?.csrf||'';
  const save=async()=>{setBusy(true);setStatus('正在保存…');try{const result=await api('/notifications/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),email,enabled})});setStatus(result?.message||'接收设置已保存');await load()}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const sendVerification=async()=>{setBusy(true);setStatus('正在保存邮箱并发送验证码…');try{if(email.trim().toLowerCase()!==(state?.preference?.email||'').trim().toLowerCase()){await api('/notifications/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),email,enabled:false})});await load()}const result=await api('/notifications/verify-send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf()})});setStatus(result?.message||'验证邮件已排队')}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const verify=async e=>{e.preventDefault();setBusy(true);setStatus('正在验证…');try{const result=await api('/notifications/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),code:code.trim()})});setStatus(result?.message||'邮箱验证成功');setCode('');await load()}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const savePolicy=async()=>{setBusy(true);setStatus('正在保存通知策略…');try{const result=await api('/notifications/policy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf(),events,quiet_start:Number(quietStart),quiet_end:Number(quietEnd),quiet_enabled:quietEnabled,interval_minutes:Number(notifyInterval)})});setStatus(result?.message||'通知偏好已保存')}catch(e){setStatus(e.message)}finally{setBusy(false)}};
  const hours=Array.from({length:24},(_,hour)=><option key={hour} value={hour}>{String(hour).padStart(2,'0')}:00</option>);
  return <><PageHeader eyebrow="07 / NOTIFICATIONS" title="通知设置" text="管理接收邮箱、提醒类型、免打扰时段和重复提醒频率。"/><div className="simple-grid"><Card><h2>接收邮箱</h2><div className="form-stack"><div className="notification-email-field"><label>邮箱地址<input className="input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email"/></label>{!state?.preference?.verified&&<Button variant="quiet" disabled={busy||!email||!state?.mail_ready} onClick={sendVerification}>发送验证邮件</Button>}</div><label className="check-line"><input type="checkbox" role="switch" className="ui-switch" checked={enabled} onChange={e=>setEnabled(e.target.checked)} disabled={!state?.preference?.verified}/>接收通知邮件</label>{state?.preference?.verified?<Badge tone="good">邮箱已验证</Badge>:<small className="muted">保存邮箱后发送验证码，输入验证码即可完成验证。</small>}{!state?.mail_ready&&<small className="muted">邮件服务尚未启用，请联系管理员。</small>}{!state?.preference?.verified&&<form className="notification-verify-row" onSubmit={verify}><label className="notification-code-field">邮箱验证码<input className="input" value={code} onChange={e=>setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6 位验证码"/></label><Button type="submit" busy={busy} disabled={code.trim().length<4}>验证邮箱</Button></form>}{status&&<div className={`inline-feedback${/失败|错误|无效|超时|异常/.test(status)?' danger-text':' success-text'}`} role={/失败|错误|无效|超时|异常/.test(status)?'alert':'status'}>{status}</div>}<Button disabled={busy||!email} onClick={save}>{busy?'请稍候…':'保存邮箱设置'}</Button></div></Card><Card><h2>提醒偏好</h2><div className="form-stack"><div className="notification-event-options">{[['account_alert','账号需要验证'],['account_recovered','账号恢复正常'],['task_failed','任务执行异常'],['expiry','订阅即将到期']].map(([id,label])=><label className="check-line" key={id}><input type="checkbox" checked={events.includes(id)} onChange={e=>setEvents(v=>e.target.checked?[...new Set([...v,id])]:v.filter(x=>x!==id))}/>{label}</label>)}</div><label className="check-line"><input type="checkbox" role="switch" className="ui-switch" checked={quietEnabled} onChange={e=>setQuietEnabled(e.target.checked)}/>开启每日免打扰</label><div className="form-grid"><label>开始时间<Select className="input" value={quietStart} onChange={e=>setQuietStart(e.target.value)}>{hours}</Select></label><label>结束时间<Select className="input" value={quietEnd} onChange={e=>setQuietEnd(e.target.value)}>{hours}</Select></label></div><label>重复提醒间隔<Select className="input" value={notifyInterval} onChange={e=>setNotifyInterval(e.target.value)}><option value={15}>15 分钟</option><option value={60}>1 小时</option><option value={180}>3 小时</option></Select></label><small className="muted">免打扰时段内延迟提醒；验证邮件不受此设置影响。</small><Button variant="quiet" disabled={busy} onClick={savePolicy}>保存提醒偏好</Button></div></Card></div></>;
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
      <span className="membership-plan-notice-mark"><StatusIcon kind="info"/></span>
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
  const navigate=useNavigate(); const [notice,setNotice]=useState(''); const [code,setCode]=useState(''); const [redeeming,setRedeeming]=useState(false); const [remote,setRemote]=useState(null); const [loading,setLoading]=useState(false); const [busy,setBusy]=useState(false); const [supportOpen,setSupportOpen]=useState(false); const [redemptionPreview,setRedemptionPreview]=useState(null); const [previewing,setPreviewing]=useState(false);
  const map={subscription:['05 / MEMBERSHIP','会员订阅','兑换码开通、查看有效期与任务额度。'], 'account-center':['06 / ACCOUNT','账户中心','管理平台账号和当前登录会话。'],attention:['08 / ATTENTION','待处理事项','需要你确认或处理的账号与执行记录。'],help:['09 / GUIDE','使用帮助','绑定、创建计划与发送结果核对。']}; const [ey,title,text]=map[type];
  const reads=useRef({generation:0,controller:null});
  useEffect(()=>()=>{reads.current.generation++;reads.current.controller?.abort()},[type]);
  const load=async()=>{reads.current.controller?.abort();const controller=new AbortController();reads.current.controller=controller;const generation=++reads.current.generation;setLoading(true);try{const value=await api(type==='subscription'?'/api/subscription':type==='account-center'?'/api/account-center':'/api/attention',{signal:controller.signal});if(generation===reads.current.generation)setRemote(value)}catch(e){if(e.name!=='AbortError'&&generation===reads.current.generation)setNotice(e.message)}finally{if(generation===reads.current.generation)setLoading(false)}};
  useEffect(()=>{setNotice('');if(['subscription','account-center','attention'].includes(type))load()},[type]);
  const membership=remote?.membership||data.membership||{};
  const previewRedemption=async()=>{if(!code.trim())return setNotice('请输入兑换码');setPreviewing(true);setNotice('正在读取兑换内容…');try{const form=new FormData();form.append('csrf_token',remote?.csrf||data.csrf||'');form.append('code',code.trim());const value=await api('/subscription/preview',{method:'POST',body:form});setRedemptionPreview(value.preview||null);setNotice(value.message||'请核对兑换内容')}catch(error){setRedemptionPreview(null);setNotice(error.message)}finally{setPreviewing(false)}};
  const redeem=async e=>{e.preventDefault();if(!code.trim())return setNotice('请输入兑换码');if(!redemptionPreview){await previewRedemption();return}setRedeeming(true);setNotice('正在验证兑换码…');try{const value=await api('/api/subscription/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||'',code:code.trim()})});if(!value?.ok)throw new Error(value?.message||'兑换失败');setCode('');setRedemptionPreview(null);setNotice(value.message||'兑换成功，订阅额度已更新。');await load()}catch(error){setNotice(error.message||'兑换码无效或已使用')}finally{setRedeeming(false)}};
  const revokeSessions=async()=>{if(!await confirmModal({title:'退出其他设备',eyebrow:'ACCOUNT SECURITY',message:'当前设备会保持登录，其他网站登录会话将立即失效。'}))return;setBusy(true);try{const value=await api('/api/account-center/revoke-sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||''})});setNotice(value?.message||'其他登录会话已退出。');await load()}catch(e){setNotice(e.message)}finally{setBusy(false)}};
  const markRead=async itemId=>{setBusy(true);try{const value=await api('/api/attention/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:remote?.csrf||data.csrf||'',item_id:itemId})});setRemote(v=>({...v,items:value.items||[]}))}catch(e){setNotice(e.message)}finally{setBusy(false)}};
  const date=value=>value?new Date(value).toLocaleString('zh-CN',{dateStyle:'medium',timeStyle:'short'}):'—';
  let body;
  if(type==='subscription') body=<><Card className="subscription-current"><h2>{membership.status_label||'尚未开通'}</h2><p>{membership.active?`有效期至 ${date(membership.valid_until)}。已绑定 ${membership.account_count??data.accounts?.length??0} 个账号，启用任务 ${membershipTaskUsage(membership)??membership.task_count??data.tasks?.length??0} / ${membership.task_limit_label||membership.max_tasks||'不限'}。`:membershipIsFreeTier(membership)?`免费版可绑定 ${membership.max_accounts||1} 个抖音账号，并创建 ${membership.task_limit_label||membership.max_tasks||1} 个好友任务。需要更多任务时兑换订阅即可。`:'联系客服购买后获取兑换码，或直接输入已有兑换码。'}</p><MembershipPlanNotice membership={membership}/><form className="redeem-form" onSubmit={redeem}><label className="redemption-field"><span>兑换码</span><input className="input redemption-input" value={code} onChange={e=>{setCode(e.target.value);setRedemptionPreview(null)}} placeholder="输入管理员提供的兑换码" autoComplete="off" disabled={redeeming}/></label><Button type="submit" busy={redeeming||previewing} disabled={!code.trim()}>{redemptionPreview?'确认兑换':'检查兑换码'}</Button></form>{redemptionPreview&&<div className="subscription-preview" role="status"><b>兑换预览</b><span>增加 {redemptionPreview.duration_days} 天 · {redemptionPreview.max_accounts} 个账号 · {redemptionPreview.max_tasks>=UNLIMITED_TASKS?'不限':redemptionPreview.max_tasks} 个任务</span><small>{redemptionPreview.quota_effect==='after_current_term'?'新额度将在当前订阅到期后生效':'额度立即生效'} · 预计到期 {date(redemptionPreview.valid_until)}</small></div>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}<button type="button" className="support-inline-link" onClick={()=>setSupportOpen(true)}>没有兑换码？联系客服购买并开通</button></Card><Card className="subscription-plans"><h2>可选套餐</h2><div className="form-stack">{(remote?.plans||data.plans||[]).map(plan=><div className="product-row" key={plan.id}><div><b>{plan.name} · ¥{plan.price_yuan} / {planBillingLabel(plan)}</b><p>{planDurationLabel(plan)} · {planTaskLabel(plan)}</p></div></div>)}</div></Card><ContactSupportCard plans={data.plans||[]} onOpen={()=>setSupportOpen(true)}/><Card className="subscription-history"><h2>开通记录</h2>{remote?.grants?.length?<div className="form-stack">{remote.grants.map(grant=><div className="product-row" key={grant.id}><div><b>{grant.source==='redemption_code'?'兑换码开通':'管理员开通'}</b><p>{date(grant.valid_from)} 至 {date(grant.valid_until)}</p></div></div>)}</div>:<p className="muted">{loading?'正在读取记录…':'暂无开通记录。'}</p>}</Card></>;
  else if(type==='account-center') body=<><Card><h2>{data.user?.name||'账户'}</h2><p>账户密码和网站登录会话由服务器保护。退出网站会话不会影响云端抖音账号和续火任务。</p><div className="quick-links"><Link to="/change-password" className="text-action">修改网站密码<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link><Link to="/notifications" className="text-action">通知设置<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link><Link to="/subscription" className="text-action">查看会员<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div><form method="post" action="/logout"><input type="hidden" name="csrf_token" value={remote?.csrf||data.csrf||''}/><Button type="submit" variant="quiet">退出当前设备</Button></form></Card><Card><div className="section-head"><div><h2>有效登录会话</h2></div><Button variant="quiet" disabled={busy||loading||!(remote?.sessions||[]).some(s=>!s.current)} onClick={revokeSessions}>退出其他设备</Button></div>{remote?.sessions?.length?<div className="form-stack">{remote.sessions.map(session=><div className="product-row" key={session.id}><div><b>{session.current?'当前设备':'其他登录设备'}</b><p>登录于 {date(session.created_at)} · 到期 {date(session.expires_at)}</p></div>{session.current&&<Badge tone="good">正在使用</Badge>}</div>)}</div>:<p className="muted">{loading?'正在读取会话…':'没有有效会话。'}</p>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}</Card></>;
  else if(type==='attention') {const items=asList(remote?.items);body=<><Card><div className="section-head"><div><h2>站内通知 · {items.filter(item=>!item.read).length} 条未读</h2></div><Button variant="quiet" disabled={busy||!items.some(item=>!item.read)} onClick={()=>markRead('all')}>全部标为已读</Button></div><p className="muted-copy">同一任务的同类异常会合并展示。标为已读不会更改执行结果，也不会触发重发。</p>{items.length?<div className="form-stack">{items.map(item=><article className={`product-row ${item.read?'is-read':''}`} key={item.id}><div className="grow"><b>{item.title}</b><p>{item.detail}</p><small>{date(item.at)} · {item.read?'已读':'未读'}</small></div><div className="row-actions"><Link to={item.url} className="text-action">查看详情<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link>{!item.read&&<Button variant="quiet" disabled={busy} onClick={()=>markRead(item.id)}>标为已读</Button>}</div></article>)}</div>:<p className="muted">{loading?'正在读取待处理事项…':'暂时没有新的通知。'}</p>}{notice&&<div className="inline-feedback" role="status">{notice}</div>}</Card></>}
  else body=<><Card><h2>第一次使用</h2><ol><li>先绑定 1 个抖音账号，免费版也可以直接开始。</li><li>在手机上确认扫码，并完成抖音要求的安全验证。</li><li>同步已有会话后，可在已同步范围按昵称或备注选择好友；这不代表抖音全部好友。免费版可创建 1 个好友任务。</li><li>需要更多好友任务时，再开通订阅；确认北京时间和消息类型后创建每日计划。</li><li>查看执行记录。待核实的消息请先到抖音聊天确认，不要直接补发。</li></ol><Button variant="quiet" onClick={()=>navigate('/accounts')}>去绑定账号</Button></Card><Card><h2>常见问题</h2><div className="form-stack"><AnimatedDetails><summary>扫码慢、密码验证或手机号验证怎么办？</summary><p>先查看排队和验证提示，按抖音当前页面要求操作并等待结果。不要连续提交验证码。云端出现冷却提示时等待平台解除。</p></AnimatedDetails><AnimatedDetails><summary>“已发出”“待核实”“未发出”有什么区别？</summary><p>已发出表示系统确认发送完成；待核实表示已提交但未取得可靠确认，请先核对聊天记录；未发出表示这次没有发出去，可根据执行详情决定是否补发。</p></AnimatedDetails><AnimatedDetails><summary>账号恢复后会补发错过的消息吗？</summary><p>不会。恢复后继续执行后续计划，不补发历史消息。手动暂停的任务需要你手动恢复。</p></AnimatedDetails><AnimatedDetails><summary>续费和任务额度如何计算？</summary><p>未到期时续费从原到期时间顺延；已到期时从本次开通时间起算。到期后保留 1 个免费好友任务执行，重新订阅后恢复全部任务额度。</p></AnimatedDetails><AnimatedDetails><summary>不限好友是否表示同时发送？</summary><p>不限好友指套餐允许设置的目标数量。同一抖音账号的发送、登录和同步按顺序执行，集中在同一时间的任务可能排队。建议分散发送时间；系统保留已购买额度，不会因任务较多自动降低套餐权益。抖音仍可能要求用户本人完成安全验证。</p></AnimatedDetails><AnimatedDetails><summary>反馈问题时需要提供什么？</summary><p>通过原购买渠道联系管理员，说明用户名、发生时间、页面、期望与实际结果；执行问题请附诊断编号。不要发送密码、验证码、登录 Cookie 或邮件授权码。</p><Link to="/attention" className="text-action">查看待处理事项<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></AnimatedDetails></div></Card></>;
  return <><PageHeader eyebrow={ey} title={title} text={text} actions={['subscription','account-center','attention'].includes(type)?<Button variant="quiet" disabled={loading} onClick={load}>刷新</Button>:null}/><div className={`simple-grid simple-page-${type}`}>{body}</div>{type==='help'&&<ContactSupportCard plans={data.plans||[]} onOpen={()=>setSupportOpen(true)}/>}<ContactSupportModal plans={data.plans||[]} open={supportOpen} onClose={()=>setSupportOpen(false)}/></>;
}

function App() {
  useEffect(() => { const overlay=installSelectOverlay(); return () => overlay?.destroy?.(); }, []);
  const [data,setData]=useState(null);
  const [readState,setReadState]=useState({busy:false,updated:null});
  const [error,setError]=useState('');
  const location=useLocation();
  const authKind=location.pathname==='/login'?'login':location.pathname==='/register'?'register':location.pathname==='/forgot-password'?'forgot':location.pathname==='/change-password'?'change':null;
  const readRef=useRef({generation:0,controller:null});
  const adminScope=location.pathname.startsWith('/admin');
  const refresh=React.useCallback(()=>{
    const read=readRef.current;read.controller?.abort();
    const generation=++read.generation;const controller=new AbortController();read.controller=controller;
    setError('');setReadState(current=>({...current,busy:true}));
    return api(adminScope?'/api/admin/bootstrap':'/api/app/bootstrap',{signal:controller.signal})
      .then(value=>{const current=normalizeBootstrap(value);if(read.generation===generation&&!controller.signal.aborted){setData(current);setReadState({busy:false,updated:Date.now()})}return current})
      .catch(error=>{if(read.generation===generation&&error.name!=='AbortError'){setError(error.message);setReadState(current=>({...current,busy:false}))}return null});
  },[adminScope]);
  useEffect(()=>{if(!authKind)refresh();return()=>readRef.current.controller?.abort()},[refresh,authKind]);
  useEffect(()=>{
    if(authKind)return;
    let last=Date.now();
    const visible=()=>{if(document.visibilityState==='visible'&&Date.now()-last>=30000){last=Date.now();refresh()}};
    const timer=setInterval(visible,60000);document.addEventListener('visibilitychange',visible);
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible)};
  },[refresh,authKind]);
  if(authKind)return <React.Suspense fallback={<div className="loading-screen"><LoadingState/></div>}><AuthPage kind={authKind} api={api} Logo={Logo} ServiceIndicator={ServiceIndicator}/></React.Suspense>;
  if(error&&!data)return <div className="loading-screen"><div className="loading-mark">✦</div><ErrorState message={error} onRetry={()=>refresh().catch(()=>{})}/><a href="/login">返回登录</a></div>;
  if(!data)return <WorkspaceSkeleton/>;
  const path=location.pathname;
  let page;
  if(path.startsWith('/admin'))page=<React.Suspense fallback={<><PageHeader title="管理工作区"/><ListSkeleton label="正在加载管理页面…" rows={4}/></>}><Admin data={data} onRefresh={refresh}/></React.Suspense>;
  else if(path==='/dashboard'||path==='/')page=<Dashboard data={data} onRefresh={refresh}/>;
  else if(path==='/studio'||path==='/tasks')page=<Studio data={data} onRefresh={refresh}/>;
  else if(path==='/accounts')page=<Accounts data={data} onRefresh={refresh}/>;
  else if(path==='/runs')page=<Runs data={data}/>;
  else if(path.startsWith('/tasks/')&&path.endsWith('/edit'))page=<TaskEdit data={data} id={path.split('/')[2]} onRefresh={refresh}/>;
  else if(path==='/notifications')page=<Notifications data={data}/>;
  else if(path==='/subscription'||path==='/account-center'||path==='/attention'||path==='/help')page=<SimplePage data={data} type={path.slice(1)}/>;
  else page=<Dashboard data={data}/>;
  return <AppShell data={data}><div className="workspace-read-status"><ReadStatus busy={readState.busy} error={error} lastUpdated={readState.updated}/></div>{error&&<div className="refresh-notice" role="alert"><span>暂时无法更新，当前显示上次读取的内容。{error}</span><Button variant="quiet" onClick={refresh}>重试读取</Button></div>}{page}</AppShell>;
}

createRoot(document.getElementById('app')).render(<BrowserRouter><RouteErrorBoundary><App/><FeedbackHost/></RouteErrorBoundary></BrowserRouter>);

function SupportReference({value}) {
  const [copied,setCopied]=useState(false);
  return <button type="button" className="support-inline-link" onKeyDown={event=>event.stopPropagation()} onClick={async event=>{event.stopPropagation();try{await navigator.clipboard.writeText(value);setCopied(true)}catch{setCopied(false)}}}>{copied?'排障编号已复制':`复制排障编号：${value}`}</button>;
}
