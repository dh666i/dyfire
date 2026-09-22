import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter, Link, useLocation, useNavigate} from 'react-router-dom';
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import './react.css';
import './react-polish.css';

const asList = value => Array.isArray(value) ? value.filter(Boolean) : [];
const displayText = (value, fallback='') => typeof value === 'string' && value.trim() ? value : fallback;
const displayName = (value, fallback='抖音账号') => displayText(value, fallback);
const initial = (value, fallback='·') => displayName(value, fallback).slice(0, 1);
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
    membership: source.membership && typeof source.membership === 'object' ? source.membership : {},
    operations: source.operations && typeof source.operations === 'object' ? source.operations : {},
    mail: {...mail, events: asList(mail.events), deliveries: asList(mail.deliveries)},
  };
}

const routes = [
  ['/dashboard','首页','⌂'], ['/studio','续火任务','✦'], ['/accounts','抖音账号','◉'],
  ['/runs','执行记录','↻'], ['/subscription','订阅管理','◇'], ['/account-center','账户中心','◎'],
];
const adminRoutes = [
  ['/admin/overview','业务概览','▦'], ['/admin/today','今日发送','◷'],
  ['/admin/users','用户管理','♙'], ['/admin/subscriptions','订阅管理','◇'], ['/admin/codes','兑换码','⌁'],
  ['/admin/tasks','任务管理','≡'], ['/admin/runs','执行记录','↻'],
  ['/admin/operations','运维监控','▥'], ['/admin/diagnostics','执行诊断','⌁'], ['/admin/audit','审计日志','≋'], ['/admin/settings','系统设置','⚙'],
];

const previewRequested = import.meta.env.DEV && new URLSearchParams(window.location.search).has('preview');
let previewScan = null;

function previewData() {
  const account = {id:'preview-account', name:'我的抖音账号', profile:{nickname:'火花用户'}, health:{state:'valid', label:'登录有效'}, validation_state:'valid'};
  const tasks = [
    {id:'preview-task-1', accountId:account.id, name:'小琪', sec_uid:'preview-friend-1', avatar:'', kind:'续火花表情', time:'09:30', enabled:true},
    {id:'preview-task-2', accountId:account.id, name:'阿杰', sec_uid:'preview-friend-2', avatar:'', kind:'续火花表情', time:'12:20', enabled:true},
    {id:'preview-task-3', accountId:account.id, name:'郭琪', sec_uid:'preview-friend-3', avatar:'', kind:'文字消息', time:'18:00', enabled:false, message:'今天也要记得续上火花。'},
  ];
  const runs = [
    {id:'preview-run-1', name:'小琪', time:'今天 09:30', kind:'续火花表情', stage:'消息确认', status:'success', label:'已发出'},
    {id:'preview-run-2', name:'阿杰', time:'今天 12:20', kind:'续火花表情', stage:'消息确认', status:'success', label:'已发出'},
    {id:'preview-run-3', name:'郭琪', time:'昨天 18:00', kind:'文字消息', stage:'账号复核', status:'failed', label:'未发出', error:'账号需要重新确认登录'},
  ];
  return {
    user:{name:'演示用户', role:new URLSearchParams(window.location.search).has('admin')?'admin':'user'}, csrf:'preview-csrf',
    accounts:[account], tasks, runs, recentRuns:runs.map((run,index)=>({id:run.id,target:run.name,owner:'演示用户',time:run.time,status:run.label,tone:run.status==='success'?'good':'danger'})),
    membership:{active:true, can_add_task:true, max_tasks:10, days_remaining:27, status_label:'专业版 · 有效中'},
    counts:{users:18, accounts:26, tasks:74, runs:1260},
    users:[{id:'preview-user-1',username:'演示用户',role:'user',status:'active',created:'2026-09-01'},{id:'preview-user-2',username:'管理员',role:'admin',status:'active',created:'2026-08-21'}],
    subscriptions:[{id:'preview-user-1',username:'演示用户',account_count:1,task_count:3,membership_status:'active',membership_label:'有效',valid_until:'2026-10-18',max_accounts:2,max_tasks:10}],
    redemptionCodes:[{id:'DYFIRE-DEMO-30',status:'待兑换',expires_at:'2026-10-20',max_accounts:1,max_tasks:10}],
    groups:[{owner_id:'preview-user-1',account_id:account.id,username:'演示用户',remark:'我的抖音账号',nickname:'火花用户',total:3,enabled:2,validation_state:'valid'}],
    operations:{refresh_interval_seconds:30,system:{scope_label:'本地预览服务器',cpu:{percent:18},memory:{percent:42},disk:{percent:37}},services:{worker:{status:'运行中',label:'运行中'}}},
    audit:[{id:'preview-audit-1',event:'任务发送',subject_type:'task_run',created_at:'2026-09-21T09:30:00+08:00',detail:'小琪 · 已发出'}],
    mail:{enabled:false,host:'',port:465,mode:'ssl',username:'',sender:'',sender_name:'火花守护',password_configured:false,events:[],deliveries:[]},
  };
}

function mockApi(path, options={}) {
  const data = previewData();
  if (path === '/api/app/bootstrap' || path === '/api/admin/bootstrap') return Promise.resolve(data);
  if (path === '/api/notifications') return Promise.resolve({csrf:data.csrf, preference:{email:'',enabled:false,verified:false}, policy:{events:['account_alert','account_recovered','task_failed','expiry']}});
  if (path === '/api/admin/updates') return Promise.resolve({configured:true,current:'0.2.27',online:true,available:null});
  if (path === '/api/admin/diagnostics') return Promise.resolve({backup:{status:'ok'},items:data.runs.map((run,index)=>({id:run.id,name:run.name,status:run.status,stage:run.stage,attempt:1,total:index===2?18:7,error_summary:run.error||''}))});
  if (path.startsWith('/api/admin/tasks/groups/')) return Promise.resolve({items:data.tasks.map(task=>({...task,account:'我的抖音账号',username:'演示用户'}))});
  if (path.includes('/conversations')) return Promise.resolve({items:[
    {name:'小琪',nickname:'小琪',remark_name:'工作伙伴',sec_uid:'preview-friend-1'},
    {name:'阿杰',nickname:'阿杰',remark_name:'',sec_uid:'preview-friend-2'},
    {name:'郭琪',nickname:'郭琪',remark_name:'郭琪',sec_uid:'preview-friend-3'},
    {name:'小林',nickname:'林林',remark_name:'',sec_uid:'preview-friend-4'},
  ]});
  if (path === '/api/studio/runs') return Promise.resolve({items:data.runs});
  if (path === '/accounts/scan') { previewScan={id:'preview-scan',status:'waiting',message:'请使用抖音 App 扫码并在手机确认'}; return Promise.resolve(previewScan); }
  if (path.startsWith('/accounts/scan/preview-scan/qr')) return Promise.reject(new Error('预览模式不提供真实二维码'));
  if (path.startsWith('/accounts/scan/preview-scan')) return Promise.resolve(previewScan||{id:'preview-scan',status:'waiting',message:'预览登录流程'});
  if (path.startsWith('/admin/settings/mail/')) return Promise.resolve({message:'预览模式：设置未提交到服务器'});
  return Promise.resolve({ok:true, message:'预览模式：操作已模拟'});
}

async function api(path, options={}) {
  if (previewRequested) return mockApi(path, options);
  let response;
  try {
    response = await fetch(path, {credentials:'same-origin', cache:'no-store', ...options});
  } catch (error) {
    if (import.meta.env.DEV) return mockApi(path, options);
    throw error;
  }
  if (response.status === 401) { window.location.assign('/login'); throw new Error('登录状态已过期'); }
  const type = response.headers.get('content-type') || '';
  const value = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    // Vite returns a 5xx response when its local API proxy cannot reach the
    // backend. Keep the dev preview usable without changing production errors.
    if (import.meta.env.DEV && response.status >= 500) return mockApi(path, options);
    throw new Error(typeof value?.detail === 'string' ? value.detail : value?.message || '请求未完成');
  }
  return value;
}

function Icon({children}) { return <span className="icon" aria-hidden="true">{children}</span>; }
function BellIcon() { return <svg className="bell-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>; }
function Logo() {
  const [missing, setMissing] = useState(false);
  return <Link className="brand" to="/dashboard" aria-label="火花首页"><span className="brand-mark">{missing?'✦':<img src="/static/spark-flame.png" alt="" onError={()=>setMissing(true)} />}</span><span className="brand-copy"><b>火花</b><small>抖音自动续火花服务</small></span></Link>;
}
function Button({children, variant='', type='button', ...props}) { return <button type={type} className={`button ${variant}`} {...props}>{children}</button>; }
function Badge({children, tone='neutral'}) { return <span className={`badge ${tone}`}><i />{children}</span>; }
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
function Empty({title, text, action}) { return <div className="empty"><div className="empty-mark">✦</div><h3>{title}</h3>{text && <p>{text}</p>}{action}</div>; }
function Drawer({open, title, eyebrow, onClose, children, wide=false, centered=false}) {
  const closeRef=useRef(null);
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
    const onKey=e=>{if(e.key==='Escape')onCloseRef.current?.()};
    window.addEventListener('keydown',onKey);
    const focusTimer=window.setTimeout(()=>closeRef.current?.focus(),0);
    return ()=>{window.clearTimeout(focusTimer);document.body.style.overflow=previous;document.body.classList.remove('drawer-open');window.removeEventListener('keydown',onKey);restoreRef.current?.focus?.()};
  },[open]);
  const requestClose=()=>{setClosing(true);if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);closeTimerRef.current=window.setTimeout(()=>{setMounted(false);setClosing(false);onCloseRef.current?.()},220)};
  if (!mounted) return null;
  return <div className={`drawer-backdrop ${centered?'drawer-centered-backdrop':''} ${closing?'is-closing':''}`} onMouseDown={e=>e.target===e.currentTarget&&requestClose()}><aside className={`drawer ${wide?'drawer-wide':''} ${centered?'drawer-centered':''} ${closing?'is-closing':''}`} role="dialog" aria-modal="true" aria-labelledby="drawer-title"><header className="drawer-head"><div><span className="eyebrow">{eyebrow}</span><h2 id="drawer-title">{title}</h2></div><button ref={closeRef} type="button" className="icon-button" onClick={requestClose} aria-label="关闭">×</button></header>{children}</aside></div>;
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

function AppShell({data, children, refresh}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [theme,setTheme] = useState(()=>localStorage.getItem('dyfire-theme') || 'dark');
  const [toast,setToast] = useState('');
  const user = data?.user || {};
  const userName = displayName(user.name, '用户');
  const admin = user.role === 'admin';
  const isActive = href => location.pathname === href || (href === '/studio' && location.pathname.startsWith('/tasks')) || (href === '/admin/tasks' && location.pathname.startsWith('/admin/tasks/groups/'));
  const dockRoutes=admin?adminRoutes.slice(0,4):routes.slice(0,4);
  useEffect(()=>{document.documentElement.dataset.theme=theme; localStorage.setItem('dyfire-theme',theme)},[theme]);
  useEffect(()=>{if (location.state?.toast) { setToast(location.state.toast); navigate(location.pathname,{replace:true,state:{}}); }},[location,navigate]);
  const logout = () => { const form=document.createElement('form'); form.method='POST'; form.action='/logout'; const input=document.createElement('input'); input.name='csrf_token'; input.value=data?.csrf||''; form.append(input); document.body.append(form); form.submit(); };
  const navLink=([href,label,icon])=><Link key={href} className={isActive(href)?'active':''} aria-current={isActive(href)?'page':undefined} to={href}><Icon>{icon}</Icon>{label}</Link>;
  const dockLink=([href,label,icon])=><Link key={href} className={isActive(href)?'active':''} aria-current={isActive(href)?'page':undefined} to={href}><Icon>{icon}</Icon><small>{label}</small></Link>;
  return <div className="app-shell">
    <aside className="sidebar"><Logo/><div className="sidebar-label">工作区</div><nav>{routes.map(navLink)}</nav>{admin&&<><div className="sidebar-label admin-label">管理</div><nav>{adminRoutes.map(navLink)}</nav></>}<div className="sidebar-bottom"><button type="button" className="theme-toggle" onClick={()=>setTheme(theme==='dark'?'light':'dark')}><span>{theme==='dark'?'☼':'◐'}</span>{theme==='dark'?'浅色模式':'深色模式'}</button><button type="button" className="user-chip" onClick={()=>navigate('/account-center')}><span className="avatar avatar-small">{initial(userName).toUpperCase()}</span><span><b>{userName}</b><small>{admin?'管理员':'个人账户'}</small></span><span aria-hidden="true">···</span></button><button type="button" className="logout-link" onClick={logout}>退出登录</button></div></aside>
    <main className="main-shell" id="workspace"><header className="mobile-head"><Logo/><div><button type="button" className="icon-button" onClick={()=>setTheme(theme==='dark'?'light':'dark')} aria-label="切换主题">{theme==='dark'?'☼':'◐'}</button><button type="button" className="icon-button" onClick={()=>navigate('/account-center')} aria-label="账户中心">◎</button></div></header><div className="global-utility"><button type="button" className="utility-button" onClick={()=>navigate('/attention')} aria-label="查看待处理事项"><BellIcon/><i/></button><button type="button" className="utility-account" onClick={()=>navigate('/account-center')}><span className="avatar avatar-small">{initial(userName).toUpperCase()}</span><span>{userName}</span><b className="account-chevron" aria-hidden="true"/></button></div><div className="main-inner"><div className="route-view" key={location.pathname}>{children}</div></div></main>
    <nav className="mobile-dock" aria-label="移动端主导航">{dockRoutes.map(dockLink)}</nav><Toast message={toast} onClose={()=>setToast('')}/>
  </div>;
}

function PageHeader({eyebrow, title, text, actions}) { return <header className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{text&&<p>{text}</p>}</div>{actions&&<div className="page-actions">{actions}</div>}</header>; }
function Metric({label,value,detail,tone=''}) { return <Card className="metric"><span>{label}</span><strong className={tone}>{value}</strong>{detail&&<small>{detail}</small>}</Card>; }
function AccountStatus({account={}, onAction}) { const health=account.health||{}; const name=displayName(account.name); const good=health.state==='valid'||account.validation_state==='valid'; const tone=good?'good':(health.state==='verification'||health.blocked?'danger':'warn'); return <div className="account-status-row"><span className="avatar">{account.profile?.avatar_url?<img src={account.profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><b>{name}</b><small>{account.profile?.nickname && account.profile.nickname!==name?account.profile.nickname+' · ':''}{health.label||'等待复核'}</small></div><Badge tone={tone}>{health.label||'等待复核'}</Badge><Button variant="quiet" onClick={()=>onAction?.(account)}>{good?'查看':'处理'}</Button></div>; }

function Dashboard({data}) {
  const navigate=useNavigate();
  const runs=asList(data?.recentRuns?.length ? data.recentRuns : data?.runs);
  const todayRuns=asList(data?.runs);
  const tasks=asList(data?.tasks);
  const accounts=asList(data?.accounts);
  const success=todayRuns.filter(r=>r.status==='success').length;
  const failed=todayRuns.filter(r=>r.status==='failed').length;
  const valid=accounts.filter(a=>a.health?.state==='valid'||a.validation_state==='valid').length;
  const healthScore=accounts.length?Math.round(valid/accounts.length*100):0;
  const contacts=[...new Map(tasks.map(t=>[t?.sec_uid||`${t?.accountId||''}:${displayName(t?.name,'未命名好友')}`,t])).values()];
  const next=tasks.find(t=>t.enabled);
  const membership=data.membership||{};
  return <div className="dashboard-mosaic">
    <header className="mosaic-toolbar"><div><span className="eyebrow">DYFIRE / TODAY</span><span className="mosaic-date">北京时间 · {new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric'})}</span></div><div className="mosaic-actions"><span className="mosaic-service"><i/>服务正常</span><Button variant="quiet" onClick={()=>navigate('/runs')}>执行记录 ↗</Button><Button onClick={()=>navigate(accounts.length?'/studio':'/accounts')}>{accounts.length?'创建任务':'绑定账号'} ↗</Button></div></header>
    <section className="mosaic-hero"><div className="mosaic-hero-type">自动续火服务<br/><span>每天，都在</span></div><div className="mosaic-hero-glyph">火<br/><em>花</em></div><div className="mosaic-hero-caption">每一次续火<br/>都是一次新的相遇</div><span className="mosaic-hero-mark">✦</span></section>
    <Card className="mosaic-health"><div className="mosaic-card-head"><div><span className="eyebrow">ACCOUNT HEALTH</span><h2>账号健康</h2></div><Badge tone={healthScore>=80?'good':healthScore?'warn':'neutral'}>{accounts.length?`${healthScore} / 100`:'未绑定'}</Badge></div><div className="health-score"><strong>{accounts.length?healthScore:'—'}</strong><span>分</span></div><p>{accounts.length?(healthScore>=80?'账号状态良好，续火服务运行稳定':'部分账号需要复核，请及时处理'):'绑定抖音账号后，这里会显示实时状态。'}</p><div className="health-divider"/><div className="health-metrics"><span><b>{success} / {todayRuns.length||0}</b><small>今日任务</small></span><span><b>{todayRuns.length?`${Math.round(success/todayRuns.length*100)}%`:'—'}</b><small>成功率</small></span><span><b>{failed}</b><small>待处理</small></span></div><Link className="mosaic-inline-link" to="/accounts">查看账号状态 ↗</Link></Card>
    <Card className="mosaic-tasks"><div className="mosaic-card-head"><div><span className="eyebrow">ROUTINE</span><h2>续火任务 <em>{tasks.length}</em></h2></div><Link className="mosaic-inline-link" to="/studio">查看全部 ↗</Link></div>{tasks.length?<div className="mosaic-task-list">{tasks.slice(0,6).map((t,index)=>{const name=displayName(t?.name,'未命名好友'); return <Link to={`/tasks/${t?.id||''}/edit`} className="mosaic-task-row" key={t?.id||`${name}-${index}`}><span className="avatar avatar-small">{t?.avatar?<img src={t.avatar} alt=""/>:initial(name)}</span><span className="grow"><b>{name}</b><small>抖音 · {displayText(t?.kind,'续火花表情')}</small></span><time>{displayText(t?.time,'—')}</time><Badge tone={t?.enabled?'good':'neutral'}>{t?.enabled?'待执行':'已暂停'}</Badge></Link>})}</div>:<Empty title="暂无续火任务" text="选择好友后，每位好友会有独立计划。" action={<Button onClick={()=>navigate('/studio')}>创建任务</Button>}/>}</Card>
    <aside className="mosaic-contacts"><div className="mosaic-image-placeholder" aria-hidden="true"><span>RELATION<br/>STAYS<br/>CLOSE</span></div><div className="mosaic-contact-head"><h2>目标好友</h2><strong>{contacts.length}</strong></div>{contacts.length?<div className="mosaic-contact-list">{contacts.map((t,index)=>{const name=displayName(t?.name,'未命名好友'); return <Link to={`/tasks/${t?.id||''}/edit`} key={t?.id||`${name}-${index}`}><span className="avatar avatar-small">{t?.avatar?<img src={t.avatar} alt=""/>:initial(name)}</span><b>{name}</b></Link>})}</div>:<p>创建任务后，好友会出现在这里。</p>}<div className="mosaic-contact-foot">好的关系<br/>值得被持续看见。</div></aside>
    <Card className="mosaic-runs"><div className="mosaic-card-head"><div><span className="eyebrow">DELIVERY LOG</span><h2>执行记录</h2></div><Link className="mosaic-inline-link" to="/runs">查看全部 ↗</Link></div>{runs.length?<div className="mosaic-run-list">{runs.slice(0,5).map(r=><div className="mosaic-run-row" key={r.id}><time>{r.time}</time><b>{displayName(r.name||r.target,'续火任务')}</b><span>{r.stage||'抖音'}</span><Badge tone={r.status==='success'?'good':r.status==='failed'?'danger':'warn'}>{r.label||r.status}</Badge></div>)}</div>:<Empty title="暂无近期执行记录" text="任务开始运行后，结果会显示在这里。"/>}</Card>
    <section className="mosaic-subscription"><span className="eyebrow">SUBSCRIPTION</span><h2>我的订阅</h2><strong>{membership.active?(membership.max_tasks==null?'∞':tasks.length):'—'}<small>{membership.active&&membership.max_tasks!=null?` / ${membership.max_tasks}`:''}</small></strong><p>{membership.active?(membership.max_tasks==null?'管理员账户，可管理全部续火任务。':`当前订阅可管理 ${membership.max_tasks} 位好友的自动续火任务。`):'当前未开通订阅，兑换管理员提供的兑换码即可开通。'}</p><div className="mosaic-sub-foot"><span>{membership.days_remaining!=null?`有效期至 ${membership.days_remaining} 天后`:'尚未开通'}</span><Button variant="quiet" onClick={()=>navigate('/subscription')}>管理订阅</Button></div></section>
    <section className="mosaic-create"><span className="mosaic-create-mark">✦</span><h2>创建任务</h2><p>选择好友与续费时长<br/>开启自动续火</p><Button onClick={()=>navigate(accounts.length?'/studio':'/accounts')}>＋ 立即创建</Button></section>
  </div>;
}

function ContactPicker({open,onClose,account,data,onDone}) {
  const [items,setItems]=useState([]); const [query,setQuery]=useState(''); const [loading,setLoading]=useState(false); const [selected,setSelected]=useState(new Set());
  useEffect(()=>{if(!open||!account?.id)return;setLoading(true);api(`/accounts/${account.id}/conversations`).then(v=>setItems(asList(v?.items))).catch(e=>onDone(null,e.message)).finally(()=>setLoading(false));},[open,account]);
  useEffect(()=>{if(open)setSelected(new Set())},[open]);
  const existing=new Set(asList(data?.tasks).filter(t=>t?.accountId===account?.id).map(t=>t?.sec_uid||t?.name)); const visible=items.filter(i=>[i?.name,i?.nickname,i?.remark_name,i?.unique_id,i?.short_id].filter(Boolean).join(' ').toLowerCase().includes(query.toLowerCase()));
  return <Drawer open={open} onClose={onClose} title="选择目标好友" eyebrow="SELECT CONNECTIONS" wide><div className="drawer-content"><input className="input search-input" autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入昵称或备注，搜索已同步会话"/><div className="drawer-tools"><span>{selected.size} 人已选</span><button onClick={()=>setSelected(new Set(visible.filter(i=>!existing.has(i?.sec_uid||i?.name)).map(i=>i?.sec_uid||i?.name).filter(Boolean)))}>全选当前结果</button><button onClick={()=>setSelected(new Set())}>清空</button></div>{loading?<div className="skeleton-stack"><div/><div/><div/></div>:<div className="contact-list">{visible.map((item,i)=>{const id=item?.sec_uid||item?.name||`contact-${i}`;const name=displayName(item?.name,'未命名好友');const disabled=existing.has(id);return <label className={`contact-row ${disabled?'disabled':''}`} key={id}><input type="checkbox" disabled={disabled} checked={selected.has(id)} onChange={e=>setSelected(s=>{const n=new Set(s);e.target.checked?n.add(id):n.delete(id);return n})}/><span className="avatar">{item?.avatar_url?<img src={item.avatar_url} alt=""/>:initial(name)}</span><span className="grow"><b>{name}</b><small>{disabled?'已有续火任务':item?.remark_name||item?.nickname||'已同步会话'}</small></span><span>{selected.has(id)?'✓':''}</span></label>})}</div>}{!loading&&!visible.length&&<Empty title="没有匹配的已同步会话" text="好友搜索只覆盖已同步的最近会话，请先在账号页同步。"/>}</div><footer className="drawer-foot"><span>{selected.size} 位好友</span><Button onClick={()=>{onDone([...selected].map(id=>items.find(i=>(i?.sec_uid||i?.name)===id)).filter(Boolean));onClose()}}>完成选择 ↗</Button></footer></Drawer>;
}

function Studio({data,onRefresh}) {
  const navigate=useNavigate(); const accounts=asList(data?.accounts); const tasks=asList(data?.tasks); const membership=data?.membership||{}; const [accountId,setAccountId]=useState(accounts[0]?.id||''); const [time,setTime]=useState('09:30'); const [kind,setKind]=useState('douyin_sticker'); const [message,setMessage]=useState(''); const [friends,setFriends]=useState([]); const [picker,setPicker]=useState(false); const [search,setSearch]=useState(''); const [filter,setFilter]=useState('all'); const [saving,setSaving]=useState(false); const [notice,setNotice]=useState(''); const account=accounts.find(a=>a?.id===accountId); const existing=tasks.filter(t=>t?.accountId===accountId); const shown=tasks.filter(t=>(filter==='all'||(filter==='active'?t?.enabled:!t?.enabled))&&(!search||displayName(t?.name,'').toLowerCase().includes(search.toLowerCase())));
  const submit=async e=>{e.preventDefault();if(!friends.length||!account)return setNotice('请选择一个或多个好友');if(kind==='text'&&!message.trim())return setNotice('请填写发送内容');setSaving(true);try{await api('/api/studio/tasks',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':data.csrf},body:JSON.stringify({account_id:account.id,targets:friends.map(f=>({name:f.name,sec_uid:f.sec_uid||''})),send_time:time,message_kind:kind,message_template:kind==='text'?message.trim():'续火花'})});setFriends([]);setNotice('已创建续火任务');await onRefresh()}catch(err){setNotice(err.message)}finally{setSaving(false)}};
  return <><PageHeader eyebrow="02 / ROUTINE STUDIO" title="续火任务" text="把每天的默契交给稳定、可核对的自动计划。" actions={<Button onClick={()=>document.getElementById('task-composer')?.scrollIntoView({behavior:'smooth'})}>新建任务 ↘</Button>}/><div className="studio-layout"><section className="studio-list"><div className="toolbar"><div className="segmented"><button className={filter==='all'?'selected':''} onClick={()=>setFilter('all')}>全部 <em>{tasks.length}</em></button><button className={filter==='active'?'selected':''} onClick={()=>setFilter('active')}>运行中 <em>{tasks.filter(t=>t?.enabled).length}</em></button><button className={filter==='paused'?'selected':''} onClick={()=>setFilter('paused')}>已暂停 <em>{tasks.filter(t=>!t?.enabled).length}</em></button></div><input className="input compact-search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索好友或备注"/></div>{shown.length?<div className="task-table"><div className="table-head"><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div>{shown.map((t,index)=>{const name=displayName(t?.name,'未命名好友');return <div className="task-row" key={t?.id||`${name}-${index}`}><span className="person-cell"><span className="avatar">{t?.avatar?<img src={t.avatar} alt=""/>:initial(name)}</span><span><b>{name}</b><small>{displayText(t?.kind,'续火花表情')}</small></span></span><time>{displayText(t?.time,'—')}<small>每天</small></time><Badge tone={t?.enabled?'good':'neutral'}>{t?.enabled?'已启用':'已暂停'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>navigate(`/tasks/${t?.id||''}/edit`)}>编辑</Button><Button variant="quiet" onClick={async()=>{try{await api(`/api/studio/tasks/${t?.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':data?.csrf},body:JSON.stringify({enabled:!t?.enabled})});onRefresh()}catch(e){setNotice(e.message)}}}>{t?.enabled?'暂停':'恢复'}</Button></span></div>})}</div>:<Empty title="还没有续火任务" text="先选择账号和好友，创建第一条每日计划。"/>}</section><Card className="composer-card" id="task-composer"><div className="section-head"><div><span className="eyebrow">NEW CONNECTION</span><h2>新建续火任务</h2></div><span className="step-mark">01</span></div><form onSubmit={submit} className="form-stack"><label>发送账号<select className="input" value={accountId} onChange={e=>{setAccountId(e.target.value);setFriends([])}}>{accounts.length?accounts.map((a,index)=><option key={a?.id||index} value={a?.id||''}>{displayName(a?.name)}</option>):<option>请先绑定账号</option>}</select></label><label>目标好友<div className="friend-trigger" onClick={()=>account&&setPicker(true)}>{friends.length?friends.map((f,index)=><span key={f?.sec_uid||f?.name||index} className="selected-chip">{displayName(f?.name,'未命名好友')}</span>):<span className="muted">选择一个或多个好友</span>}<span>＋</span></div></label><div className="form-grid"><label>每日发送时间<input className="input" type="time" value={time} onChange={e=>setTime(e.target.value)}/></label><label>消息类型<select className="input" value={kind} onChange={e=>setKind(e.target.value)}><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></select></label></div>{kind==='text'&&<label>发送内容<textarea className="input textarea" maxLength="300" value={message} onChange={e=>setMessage(e.target.value)} placeholder="写一句日常问候"/></label>}<div className="message-preview"><span className="preview-mark">✦</span><div><b>{kind==='text'?'自定义文字消息':'续火花表情'}</b><small>{kind==='text'?'按设定时间发送你填写的内容。':'每天定时，给选中的好友发送续火花表情。'}</small></div></div>{notice&&<div className="inline-feedback">{notice}</div>}<Button type="submit" disabled={saving||!membership.can_add_task}>{saving?'正在创建…':'创建续火任务 ↗'}</Button>{!membership.can_add_task&&<small className="muted">当前订阅不可创建新任务，请先查看订阅。</small>}</form></Card></div><ContactPicker open={picker} onClose={()=>setPicker(false)} account={account} data={data} onDone={(value,err)=>{if(err)setNotice(err);else if(value)setFriends(value)}}/></>;
}

function ScanVerification({scan,data,setScan}) {
  const [value,setValue]=useState(''); const [sending,setSending]=useState(false); const [notice,setNotice]=useState('');
  const needs=Boolean(scan?.needs_verification);
  if(!needs)return null;
  const password=scan?.verification_kind==='password';
  const submit=async e=>{e.preventDefault();if(!value.trim()||!scan?.id)return;setSending(true);setNotice('正在提交验证…');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','text');fd.append('request_id',`${Date.now()}-${Math.random().toString(36).slice(2)}`);fd.append('text',value);await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd});setValue('');setNotice('已提交，等待抖音确认');}catch(err){setNotice(err.message)}finally{setSending(false)}};
  return <form className="verification-panel" onSubmit={submit}><div className="verification-title"><span className="verification-icon">{password?'⌁':'✉'}</span><div><b>{password?'输入抖音登录密码':'短信验证码验证'}</b><small>{password?'请在此输入抖音账号密码，提交一次即可。':scan?.masked_phone?`验证码将发送至 ${scan.masked_phone}`:'请填写抖音页面要求的验证码。'}</small></div></div><label className="verification-field"><span>{password?'登录密码':'验证码'}</span><input className="input" type={password?'password':'text'} inputMode={password?'text':'numeric'} autoComplete={password?'current-password':'one-time-code'} value={value} onChange={e=>setValue(e.target.value)} placeholder={password?'输入密码':'输入验证码'}/></label><div className="verification-actions"><Button type="submit" disabled={sending||!value.trim()}>{sending?'提交中…':'提交验证'}</Button>{!password&&<span className="verification-hint">{scan?.resend_after_seconds?`${scan.resend_after_seconds}s 后可重试`:scan?.verification_sent?'验证码已发送':'等待抖音发送验证码'}</span>}</div>{notice&&<div className="inline-feedback">{notice}</div>}</form>;
}

function CloudInteraction({scan,data}) {
  const [value,setValue]=useState(''); const [busy,setBusy]=useState(false); const [status,setStatus]=useState('');
  if (data?.user?.role!=='admin' || scan?.needs_verification || document.body.dataset.dyfireFrameReady!=='true') return null;
  const submit=async e=>{
    e.preventDefault(); if(!value.trim()||busy||!scan?.id)return;
    setBusy(true); setStatus('正在发送到云端，请勿重复提交…');
    try {
      const requestId=`${Date.now()}-${Math.random().toString(36).slice(2)}`;
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
  const [state,setState]=useState('loading'); const [attempt,setAttempt]=useState(0); const preview=scan?.id==='preview-scan';
  useEffect(()=>{setState('loading');setAttempt(0)},[scan?.id]);
  const click=async e=>{if(!scan?.id)return;const rect=e.currentTarget.getBoundingClientRect();const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','click');fd.append('x',String(Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width))));fd.append('y',String(Math.max(0,Math.min(1,(e.clientY-rect.top)/rect.height))));try{await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd})}catch(err){onError?.(err.message)}};
  useEffect(()=>{
    if(preview||state==='ready'||!scan?.id)return;
    const timer=window.setTimeout(()=>{setAttempt(value=>value+1);setState('loading')},Math.min(5000,1200+attempt*250));
    return ()=>window.clearTimeout(timer);
  },[preview,state,attempt,scan?.id]);
  if(preview) return <div className="scan-stage frame-ready"><div className="preview-qr" aria-label="预览二维码"><span>DYFIRE</span><b>扫码预览</b></div></div>;
  return <div className={`scan-stage ${state==='ready'?'frame-ready':''}`}><img key={`${scan?.id}-${attempt}`} onLoad={()=>{setState('ready');onReady?.();document.body.dataset.dyfireFrameReady='true';document.dispatchEvent(new CustomEvent('dyfire-frame-ready'))}} onError={()=>setState('loading')} onClick={click} src={`/accounts/scan/${scan.id}/qr?attempt=${attempt}`} alt="抖音云端登录页面"/><div className="scan-loading-overlay" aria-live="polite"><span className="scan-loading-spinner"/><b>{attempt>3?'正在等待登录环境就绪':'正在准备安全登录环境'}</b><small>云端容器启动后会自动显示，请稍候，不需要重复点击。</small></div></div>;
}

function Accounts({data,onRefresh}) {
  const [scan,setScan]=useState(null); const [error,setError]=useState(''); const [busy,setBusy]=useState(false); const [frameReady,setFrameReady]=useState(false); const navigate=useNavigate();
  const accounts=Array.isArray(data?.accounts)?data.accounts:[];
  const tasks=Array.isArray(data?.tasks)?data.tasks:[];
  useEffect(()=>{if(!scan?.id)return;let timer;const poll=async()=>{try{const next=await api(`/accounts/scan/${scan.id}`);if(!next||typeof next!=='object'){setError('登录状态暂时没有返回，请重新绑定');setScan(null);return}setScan(next);if(!['succeeded','failed','expired','cancelled'].includes(next.status))timer=setTimeout(poll,1500);else if(next.status==='succeeded')onRefresh()}catch(e){setError(e.message)}};timer=setTimeout(poll,500);return()=>clearTimeout(timer)},[scan?.id,onRefresh]);
  const start=async()=>{setBusy(true);setError('');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');const v=await api('/accounts/scan',{method:'POST',body:fd});if(!v||typeof v!=='object')throw new Error('绑定服务没有返回登录状态');setScan(v)}catch(e){setError(e.message)}finally{setBusy(false)}};
  const terminal=scan&&['failed','expired','cancelled'].includes(scan.status);
  useEffect(()=>{setFrameReady(false);document.body.dataset.dyfireFrameReady='false'},[scan?.id]);
  useEffect(()=>{const ready=()=>setFrameReady(true);document.addEventListener('dyfire-frame-ready',ready);return()=>document.removeEventListener('dyfire-frame-ready',ready)},[]);
  return <><PageHeader eyebrow="03 / DOUYIN ACCOUNTS" title="抖音账号" text="集中管理登录状态、资料同步与安全复核。" actions={<Button onClick={start} disabled={busy}>{busy?'正在准备…':'绑定抖音账号 ↗'}</Button>}/>{error&&<div className="inline-feedback danger-text">{error}</div>}<div className="account-grid">{accounts.length?accounts.map(a=>{const name=String(a?.name||'抖音账号'); const profile=a?.profile||{}; const health=a?.health||{}; return <Card className="account-card" key={a.id||name}><div className="account-card-head"><span className="avatar avatar-large">{profile.avatar_url?<img src={profile.avatar_url} alt=""/>:initial(name)}</span><div className="grow"><h2>{name}</h2><small>{profile.nickname||'抖音账号'} · 绑定于此工作区</small></div><Badge tone={(health.state==='valid'||a.validation_state==='valid')?'good':'warn'}>{health.label||'等待复核'}</Badge></div><div className="account-card-meta"><span>登录状态<strong>{health.label||'未复核'}</strong></span><span>续火任务<strong>{tasks.filter(t=>t?.accountId===a?.id).length} 条</strong></span></div><div className="account-card-actions"><Button variant="quiet" onClick={()=>navigate('/studio')}>创建任务 ↗</Button><Button variant="quiet" onClick={()=>{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');api(`/accounts/${a?.id}/health-check`,{method:'POST',body:fd}).then(()=>setError('已安排登录复核')).catch(e=>setError(e.message))}}>复核状态</Button></div></Card>}):<Empty title="还没有绑定抖音账号" text="扫码后按抖音 App 提示确认登录。" action={<Button onClick={start}>开始绑定</Button>}/>}</div><Card className="account-guide"><span className="eyebrow">ACCOUNT CARE</span><h2>保持账号稳定</h2><p>系统会在发送前复核账号状态；遇到抖音安全验证时，会暂停相关计划并在这里显示处理入口。</p><div className="guide-steps"><span><b>01</b>扫码确认</span><span><b>02</b>完成验证</span><span><b>03</b>自动复核</span></div></Card><Drawer open={!!scan} onClose={()=>setScan(null)} title="绑定抖音账号" eyebrow="SECURE LOGIN" wide centered><div className="scan-panel">{scan?.status==='succeeded'?<><div className="success-mark">✓</div><h3>绑定成功</h3><p>抖音账号已安全保存，可以创建续火任务。</p><Button onClick={()=>{setScan(null);navigate('/studio')}}>去创建任务 ↗</Button></>:terminal?<><div className="success-mark failure-mark">!</div><h3>本次绑定未完成</h3><p>{scan?.message||'二维码已失效，请重新发起绑定。'}</p><Button onClick={start}>重新绑定</Button></>:<><div className="scan-progress"><span className="scan-progress-dot"/><span>安全登录通道已建立</span><span className="scan-progress-line"/><span className="scan-progress-dot active"/><span>{scan?.needs_verification?'等待验证':'等待扫码确认'}</span></div><CloudFrame scan={scan} data={data} onError={setError}/><h3>{scan?.message||'请使用抖音 App 扫码并在手机确认'}</h3><p>{scan?.needs_verification?'抖音已要求额外验证，请在下方完成一次提交。':'二维码只在当前登录窗口有效，完成确认后页面会自动更新状态。'}</p><CloudInteraction scan={scan} data={data}/><ScanVerification scan={scan} data={data} setScan={setScan}/><Button variant="quiet" onClick={()=>{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');api(`/accounts/scan/${scan?.id}/cancel`,{method:'POST',body:fd});setScan(null)}}>取消绑定</Button></>}</div></Drawer></>;
}

function Runs({data}) { const [rows,setRows]=useState(asList(data?.runs)); const [filter,setFilter]=useState(''); useEffect(()=>{api(`/api/studio/runs?status=${filter}`).then(v=>setRows(asList(v?.items))).catch(()=>{})},[filter]); return <><PageHeader eyebrow="04 / DELIVERY LOG" title="执行记录" text="每次发送都保留可核对的状态和原因。"/><div className="toolbar runs-toolbar"><div className="segmented"><button className={!filter?'selected':''} onClick={()=>setFilter('')}>全部</button><button className={filter==='success'?'selected':''} onClick={()=>setFilter('success')}>已发出</button><button className={filter==='failed'?'selected':''} onClick={()=>setFilter('failed')}>未发出</button><button className={filter==='submitted_unconfirmed'?'selected':''} onClick={()=>setFilter('submitted_unconfirmed')}>待核实</button></div></div><Card className="runs-card">{rows.length?<div className="run-list">{rows.map(r=><div className="run-row" key={r.id}><span className={`run-dot ${r.status}`}/><div className="grow"><b>{r.name}</b><small>{r.time} · {r.kind||r.stage}</small></div><Badge tone={r.status==='success'?'good':r.status==='failed'?'danger':'warn'}>{r.label||r.status}</Badge><span className="run-error">{r.error||r.guidance?.text||'计划执行正常'}</span></div>)}</div>:<Empty title="还没有执行记录" text="任务开始运行后，结果会出现在这里。"/>}</Card></>; }

function TaskEdit({data,id,onRefresh}) { const task=asList(data?.tasks).find(t=>t?.id===id); const [time,setTime]=useState(task?.time||'09:30'); const [kind,setKind]=useState(task?.kind==='文字消息'?'text':'douyin_sticker'); const [message,setMessage]=useState(task?.message||''); const [status,setStatus]=useState(''); const navigate=useNavigate(); if(!task)return <Empty title="任务不存在" action={<Button onClick={()=>navigate('/studio')}>返回续火任务</Button>} />; const save=async e=>{e.preventDefault();setStatus('正在保存…');const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('account_id',task?.accountId||'');fd.append('target_name',task?.name||'');fd.append('target_sec_uid',task?.sec_uid||'');fd.append('send_time',time);fd.append('message_kind',kind);fd.append('message_template',kind==='text'?message:'续火花');try{await api(`/tasks/${id}/edit`,{method:'POST',body:fd});setStatus('已保存');await onRefresh();navigate('/studio')}catch(err){setStatus(err.message)}};return <><PageHeader eyebrow="TASK SETTINGS" title={`编辑 ${displayName(task?.name,'未命名好友')}`} text="目标好友来自已保存任务，编辑时不会重新选择或同步好友。" actions={<Button variant="quiet" onClick={()=>navigate('/studio')}>返回任务列表</Button>}/><Card className="edit-card"><form className="form-stack" onSubmit={save}><div className="edit-target"><span className="avatar avatar-large">{task?.avatar?<img src={task.avatar} alt=""/>:initial(task?.name,'未')}</span><div><b>{displayName(task?.name,'未命名好友')}</b><small>目标好友 · 账号任务</small></div></div><label>每日发送时间<input className="input" type="time" value={time} onChange={e=>setTime(e.target.value)}/></label><label>消息类型<select className="input" value={kind} onChange={e=>setKind(e.target.value)}><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></select></label>{kind==='text'&&<label>发送内容<textarea className="input textarea" value={message} onChange={e=>setMessage(e.target.value)}/></label>}{status&&<div className="inline-feedback">{status}</div>}<Button type="submit">保存任务 ↗</Button></form></Card></>;
}

function Notifications({data}) {
  const [state,setState]=useState(null); const [status,setStatus]=useState(''); const [email,setEmail]=useState(''); const [enabled,setEnabled]=useState(false); const [events,setEvents]=useState([]);
  useEffect(()=>{api('/api/notifications').then(v=>{setState(v||{});setEmail(v?.preference?.email||'');setEnabled(!!v?.preference?.enabled);setEvents(asList(v?.policy?.events))}).catch(e=>setStatus(e.message))},[]);
  const save=async()=>{setStatus('正在保存…');try{await api('/notifications/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:state?.csrf||data?.csrf||'',email,enabled})});setStatus('接收设置已保存')}catch(e){setStatus(e.message)}};
  const savePolicy=async()=>{setStatus('正在保存通知类型…');try{await api('/notifications/policy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:state?.csrf||data?.csrf||'',events,quiet_start:23,quiet_end:8,quiet_enabled:false,interval_minutes:60})});setStatus('通知类型已保存')}catch(e){setStatus(e.message)}};
  return <><PageHeader eyebrow="07 / NOTIFICATIONS" title="通知设置" text="只在账号异常、恢复、任务失败或订阅到期时提醒。"/><div className="simple-grid"><Card><span className="eyebrow">DELIVERY ADDRESS</span><h2>接收邮箱</h2><div className="form-stack"><label>邮箱地址<input className="input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com"/></label><label className="check-line"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)} disabled={!state?.preference?.verified}/>接收异常和恢复提醒</label>{state?.preference?.verified?<Badge tone="good">邮箱已验证</Badge>:<small className="muted">保存邮箱后发送验证邮件，验证完成才能启用通知。</small>}{status&&<div className="inline-feedback">{status}</div>}<Button onClick={save}>保存设置</Button></div></Card><Card><span className="eyebrow">EVENTS</span><h2>提醒类型</h2><div className="form-stack">{[['account_alert','账号需要验证'],['account_recovered','账号恢复正常'],['task_failed','任务执行异常'],['expiry','订阅即将到期']].map(([id,label])=><label className="check-line" key={id}><input type="checkbox" checked={events.includes(id)} onChange={e=>setEvents(v=>e.target.checked?[...v,id]:v.filter(x=>x!==id))}/>{label}</label>)}<Button variant="quiet" onClick={savePolicy}>保存提醒类型</Button></div></Card></div></>;
}

function SimplePage({data,type}) {
  const navigate=useNavigate(); const [notice,setNotice]=useState(''); const [code,setCode]=useState(''); const [redeeming,setRedeeming]=useState(false);
  const map={subscription:['05 / MEMBERSHIP','会员订阅','兑换码开通、查看有效期与任务额度。'], 'account-center':['06 / ACCOUNT','账户中心','管理平台账号和当前登录会话。'],notifications:['07 / NOTIFICATIONS','通知设置','设置账号异常、恢复和到期提醒。'],attention:['08 / ATTENTION','待处理事项','需要你确认或处理的账号与执行记录。'],help:['09 / HELP','使用帮助','关于账号绑定、任务确认和稳定性的说明。']}; const [ey,title,text]=map[type];
  const membership=data.membership||{};
  const redeem=async e=>{e.preventDefault();if(!code.trim())return setNotice('请输入兑换码');setRedeeming(true);setNotice('正在验证兑换码…');try{const value=await api('/api/subscription/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf||'',code:code.trim()})});if(!value?.ok)throw new Error(value?.message||'兑换失败');setCode('');setNotice('兑换成功，订阅额度已更新。');window.setTimeout(()=>window.location.reload(),700)}catch(error){setNotice(error.message||'兑换码无效或已使用')}finally{setRedeeming(false)}};
  const main=<Card><span className="eyebrow">{type==='subscription'?'PLAN STATUS':'WORKSPACE'}</span><h2>{type==='subscription'?(membership.status_label||'尚未开通'):type==='account-center'?'登录安全':type==='notifications'?'邮件通知':type==='attention'?'当前需要关注':'火花守护'} </h2><p>{type==='subscription'?(membership.active?`有效期剩余 ${membership.days_remaining} 天，可创建 ${membership.max_tasks} 条任务。`:'兑换管理员提供的兑换码即可开通。'):type==='account-center'?'平台密码和会话由服务端保护，退出登录会立即失效当前会话。':type==='notifications'?'管理员启用邮件服务后，可验证邮箱并选择通知类型。':type==='attention'?'暂无新的待处理事项。系统会在账号需要验证或发送待核实时提醒。':'所有页面共享同一套任务、账号和执行记录数据。'}</p>{type==='subscription'&&<form className="redeem-form" onSubmit={redeem}><label className="auth-field"><span>兑换码</span><input className="input" value={code} onChange={e=>setCode(e.target.value)} placeholder="输入管理员提供的兑换码" autoComplete="off" disabled={redeeming}/></label><Button type="submit" disabled={redeeming}>{redeeming?'正在兑换…':'兑换订阅'}</Button></form>}{notice&&<div className="inline-feedback">{notice}</div>}{type==='account-center'&&<Button onClick={()=>navigate('/login')}>重新登录</Button>}{type==='help'&&<Button variant="quiet" onClick={()=>navigate('/accounts')}>去绑定账号 ↗</Button>}</Card>;
  return <><PageHeader eyebrow={ey} title={title} text={text}/><div className={`simple-grid ${type==='subscription'||type==='account-center'?'single-simple-grid':''}`}>{main}{type!=='subscription'&&type!=='account-center'&&<Card><span className="eyebrow">QUICK LINKS</span><div className="quick-links"><Link to="/dashboard">今日总览 ↗</Link><Link to="/accounts">抖音账号 ↗</Link><Link to="/studio">续火任务 ↗</Link><Link to="/runs">执行记录 ↗</Link></div></Card>}</div></>;
}

function Admin({data,onRefresh}) {
  const location=useLocation();
  const path=location.pathname;
  const isTaskGroup=path.startsWith('/admin/tasks/groups/');
  const section=isTaskGroup?'task-group':path.split('/')[2]||'overview';
  const isMail=section==='settings' && path.endsWith('/mail');
  const groupParts=isTaskGroup?path.split('/').slice(-2):[];
  const [query,setQuery]=useState('');
  const [users,setUsers]=useState(data.users||[]);
  const [update,setUpdate]=useState(null);
  const [diagnostics,setDiagnostics]=useState(null);
  const [groupDetail,setGroupDetail]=useState(null);
  const [selectedTask,setSelectedTask]=useState(null);
  const [userWorkspace,setUserWorkspace]=useState(null);
  const [feedback,setFeedback]=useState('');
  const [liveOperations,setLiveOperations]=useState(data.operations||{});
  useEffect(()=>{
    if(section==='users') api(`/api/admin/bootstrap?q=${encodeURIComponent(query)}`).then(v=>setUsers(v.users||[])).catch(()=>{});
    if(section==='settings'&&!isMail) api('/api/admin/updates').then(setUpdate).catch(e=>setFeedback(e.message));
    if(section==='diagnostics') api('/api/admin/diagnostics').then(setDiagnostics).catch(e=>setFeedback(e.message));
    if(section==='operations'){
      let active=true;
      const read=()=>api('/admin/operations/snapshot').then(value=>{if(active&&value)setLiveOperations(value)}).catch(e=>{if(active)setFeedback(e.message)});
      read();
      const timer=window.setInterval(read,30000);
      return()=>{active=false;window.clearInterval(timer)};
    }
    if(isTaskGroup && groupParts.length===2) api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}`).then(setGroupDetail).catch(e=>setFeedback(e.message));
  },[query,section,isMail,isTaskGroup,path]);
  useEffect(()=>{
    if(section==='codes'){
      const field=document.getElementById('code-tasks');
      if(field && field.value==='20'){field.defaultValue='10';field.value='10';}
    }
  },[section]);
  const title={overview:'业务概览',today:'今日发送',users:'用户管理',customers:'客户详情',subscriptions:'订阅管理',codes:'兑换码',tasks:'任务管理','task-group':'任务明细',runs:'执行记录',operations:'运维监控',diagnostics:'执行诊断',audit:'审计日志',settings:'系统设置'}[section]||'管理后台';
  const counts=data.counts||{};
  const submit=async (path, fields)=>{
    setFeedback('正在保存…');
    const normalizedFields={...fields};
    if(normalizedFields.max_tasks==='20' && (path==='/admin/redemption-codes' || path.includes('/subscription'))) normalizedFields.max_tasks='10';
    const body=new URLSearchParams({csrf_token:data.csrf,...normalizedFields});
    try { await api(path,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body}); setFeedback('已保存'); onRefresh(); }
    catch(e){ setFeedback(e.message); }
  };
  const openUser=async user=>{setFeedback('正在读取客户工作台…');try{setUserWorkspace(await api(`/api/admin/users/${user.id}/workspace`));setFeedback('')}catch(e){setFeedback(e.message)}};
  const refreshWorkspace=()=>{if(userWorkspace?.user?.id) openUser({id:userWorkspace.user.id})};
  const subscriptionFor=id=>(data.subscriptions||[]).find(item=>item.id===id)||{};
  const adminHeader=<PageHeader eyebrow={`ADMIN / ${section.toUpperCase()}`} title={title} text="管理员工作区 · 用户、任务和运行状态集中管理。" actions={<Button variant="quiet" onClick={onRefresh}>刷新数据 ↻</Button>}/>;
  return <>{adminHeader}{feedback&&<div className="inline-feedback">{feedback}</div>}
    {section==='overview'&&<><div className="metric-grid admin-metrics"><Metric label="用户" value={counts.users||0} detail="注册账户"/><Metric label="抖音账号" value={counts.accounts||0} detail="绑定数量"/><Metric label="续火任务" value={counts.tasks||0} detail="全部用户" tone="accent"/><Metric label="执行记录" value={counts.runs||0} detail="累计记录"/></div><Card><div className="section-head"><h2>最近执行</h2><Link to="/admin/runs">查看全部 ↗</Link></div><AdminRuns rows={data.recentRuns}/></Card></>}
    {section==='users'&&<Card><div className="section-head"><div><h2>客户工作台</h2><p className="muted-copy">在同一处查看账号、订阅、任务和最近执行，并直接进行管理操作。</p></div><span className="eyebrow">{users.length} USERS</span></div><div className="toolbar"><input className="input compact-search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索用户名"/></div><div className="admin-table"><div className="table-head"><span>账号</span><span>订阅 / 资源</span><span>状态</span><span>操作</span></div>{users.map(u=>{const sub=subscriptionFor(u.id);return <div className="admin-row admin-user-row" key={u.id}><button className="admin-cell-button" onClick={()=>openUser(u)}><b>{u.username}<small>{u.role==='admin'?'管理员':'普通用户'} · 注册于 {u.created}</small></b></button><span>{u.account_count??sub.account_count??0} 个账号 · {u.task_count??sub.task_count??0} 个任务<small>{u.membership_label||sub.membership_label||'未开通订阅'}</small></span><Badge tone={u.status==='active'?'good':'danger'}>{u.status==='active'?'正常':'停用'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>openUser(u)}>打开工作台</Button><Button variant="quiet" onClick={()=>api(`/api/admin/users/${u.id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})}).then(()=>{setFeedback('用户状态已更新');onRefresh()}).catch(e=>setFeedback(e.message))}>{u.status==='active'?'停用':'启用'}</Button></span></div>})}</div></Card>}
    {section==='subscriptions'&&<Card><div className="section-head"><h2>订阅与额度工作台</h2><span className="eyebrow">{(data.subscriptions||[]).length} ACCOUNTS</span></div><div className="admin-table"><div className="table-head"><span>用户</span><span>状态</span><span>有效期 / 额度</span><span>操作</span></div>{(data.subscriptions||[]).map(u=><div className="admin-row" key={u.id}><button className="admin-cell-button" onClick={()=>openUser(u)}><b>{u.username}<small>{u.account_count} 个账号 · {u.task_count} 个任务</small></b></button><Badge tone={u.membership_status==='active'?'good':u.membership_status==='expiring'?'warn':'danger'}>{u.membership_label}</Badge><span>{u.valid_until?new Date(u.valid_until).toLocaleDateString('zh-CN'):'未开通'}<small>最多 {u.max_accounts||1} 个账号 · {u.max_tasks||10} 个任务</small></span><span className="row-actions"><Button variant="quiet" onClick={()=>submit(`/admin/users/${u.id}/subscription`,{duration_days:'30',max_accounts:String(u.max_accounts||1),max_tasks:String(u.max_tasks||10)})}>续期 30 天</Button>{u.membership_status!=='inactive'&&<Button variant="danger" onClick={()=>submit(`/admin/users/${u.id}/subscription/revoke`,{})}>撤销</Button>}</span></div>)}</div></Card>}
    {section==='codes'&&<><Card><div className="section-head"><div><span className="eyebrow">ISSUE ACCESS</span><h2>生成兑换码</h2></div></div><div className="form-grid admin-code-form"><label>有效天数<input className="input" defaultValue="30" id="code-days"/></label><label>任务额度<input className="input" defaultValue="10" id="code-tasks"/></label><label>账号额度<input className="input" defaultValue="1" id="code-accounts"/></label><label>数量<input className="input" defaultValue="1" id="code-quantity"/></label></div><Button onClick={()=>submit('/admin/redemption-codes',{duration_days:document.getElementById('code-days')?.value||'30',max_tasks:document.getElementById('code-tasks')?.value||'10',max_accounts:document.getElementById('code-accounts')?.value||'1',quantity:document.getElementById('code-quantity')?.value||'1'})}>生成兑换码 ↗</Button></Card><Card><div className="section-head"><h2>最近兑换码</h2></div><div className="admin-table"><div className="table-head"><span>编号</span><span>状态</span><span>有效期</span><span>额度</span></div>{(data.redemptionCodes||[]).map(c=><div className="admin-row" key={c.id}><b>{c.id.slice(0,8)}…</b><Badge tone={c.status==='待兑换'?'good':'neutral'}>{c.status}</Badge><time>{c.expires_at?new Date(c.expires_at).toLocaleDateString('zh-CN'):'—'}</time><span>{c.max_accounts} 账号 · {c.max_tasks} 任务</span></div>)}</div></Card></>}
    {section==='tasks'&&<Card><div className="section-head"><h2>按用户与抖音账号归组</h2><span className="eyebrow">{(data.groups||[]).length} GROUPS</span></div><div className="admin-table"><div className="table-head"><span>用户 / 账号</span><span>任务数</span><span>启用</span><span>状态</span></div>{(data.groups||[]).map((g,i)=><div className="admin-row" key={i}><Link className="admin-group-link" to={`/admin/tasks/groups/${g.owner_id}/${g.account_id||'unbound'}`}><b>{g.username||'—'} · {g.remark||'账号'}<small>{g.nickname||''}</small></b></Link><span>{g.total||0}</span><span>{g.enabled||0}</span><Badge tone={g.validation_state==='valid'?'good':'warn'}>{g.validation_state||'未知'}</Badge></div>)}</div></Card>}
    {section==='task-group'&&<Card><div className="section-head"><div><span className="eyebrow">TASK GROUP</span><h2>{groupDetail?.items?.[0]?.account||'账号任务'}</h2></div><Link to="/admin/tasks">返回分组 ↗</Link></div>{groupDetail?.items?.length?<div className="admin-table"><div className="table-head"><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div>{groupDetail.items.map(item=><div className="admin-row" key={item.id}><b>{item.name}<small>{item.username} · {item.message_kind==='douyin_sticker'?'续火花表情':'文字消息'}</small></b><time>{item.time}</time><Badge tone={item.enabled?'good':'neutral'}>{item.enabled?'已启用':'已暂停'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>setSelectedTask(item)}>编辑</Button><Button variant="quiet" onClick={async()=>{try{await api(`/api/admin/tasks/${item.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,enabled:!item.enabled})});setFeedback(item.enabled?'任务已暂停':'任务已恢复');onRefresh()}catch(e){setFeedback(e.message)}}}>{item.enabled?'暂停':'恢复'}</Button><Button variant="danger" onClick={()=>{if(window.confirm(`确定删除发给 ${item.name} 的任务吗？`))api(`/api/admin/tasks/${item.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})}).then(()=>{setFeedback('任务已删除');onRefresh()}).catch(e=>setFeedback(e.message))}}>删除</Button></span></div>)}</div>:<Empty title="暂无任务" text={groupDetail?'这个账号下还没有匹配的续火任务。':'正在读取任务明细…'}/>}</Card>}
    {section==='runs'&&<Card><AdminRuns rows={data.recentRuns}/></Card>}
    {section==='today'&&<><div className="metric-grid"><Metric label="今日执行" value={data.recentRuns?.length||0} detail="最近调度记录"/><Metric label="待核实" value={(data.recentRuns||[]).filter(item=>item.status==='待核实').length} detail="需要复核" tone="accent"/></div><Card><div className="section-head"><h2>今日发送</h2><Link to="/admin/runs">查看完整记录 ↗</Link></div><AdminRuns rows={data.recentRuns}/></Card></>}
    {section==='operations'&&<><div className="metric-grid"><Metric label="CPU" value={liveOperations?.system?.cpu?.percent!=null?`${Math.round(liveOperations.system.cpu.percent)}%`:'—'} detail={liveOperations?.system?.scope_label||'服务器实时使用率'}/><Metric label="内存" value={liveOperations?.system?.memory?.percent!=null?`${Math.round(liveOperations.system.memory.percent)}%`:'—'} detail="服务器实时使用率" tone="accent"/><Metric label="磁盘" value={liveOperations?.system?.disk?.percent!=null?`${Math.round(liveOperations.system.disk.percent)}%`:'—'} detail="服务器实时使用率"/><Metric label="Worker" value={liveOperations?.services?.worker?.status==='healthy'?'运行中':liveOperations?.services?.worker?.label||'—'} detail="任务执行服务"/></div><div className="metric-grid admin-queue-metrics"><Metric label="待执行" value={liveOperations?.queue?.due_tasks??'—'} detail="当前到期任务"/><Metric label="运行中" value={liveOperations?.queue?.running_runs??'—'} detail="占用中的执行"/><Metric label="待重试" value={liveOperations?.queue?.retrying_runs??'—'} detail="延迟重试任务" tone="accent"/><Metric label="需关注" value={liveOperations?.queue?.attention_24h??'—'} detail="近 24 小时异常" tone="danger"/></div><Card><div className="section-head"><div><h2>服务器状态</h2><small className="muted">每 30 秒自动刷新</small></div><span className={`badge ${liveOperations?.services?.worker?.status==='healthy'?'good':'danger'}`}><i/>{liveOperations?.services?.worker?.status==='healthy'?'运行正常':'需要处理'}</span></div><p className="muted-copy">{liveOperations?.system?.scope_label||'当前服务器主机指标'} · 数据库 {liveOperations?.database?.size_bytes!=null?`${Math.round(liveOperations.database.size_bytes/1024/1024)} MB`:'—'} · 活跃登录 {liveOperations?.queue?.active_logins??'—'}</p></Card></>}
    {section==='audit'&&<Card><div className="section-head"><h2>审计日志</h2></div><div className="admin-table"><div className="table-head"><span>事件</span><span>资源</span><span>时间</span><span>详情</span></div>{(data.audit||[]).map(row=><div className="admin-row" key={row.id}><b>{row.event}</b><span>{row.subject_type||'—'}</span><time>{row.created_at?new Date(row.created_at).toLocaleString('zh-CN'):'—'}</time><span>{row.detail||'—'}</span></div>)}</div></Card>}
    {section==='diagnostics'&&<><Card><div className="section-head"><div><span className="eyebrow">RELIABILITY</span><h2>执行诊断</h2></div><Badge tone={diagnostics?.backup?.status==='ok'?'good':'warn'}>{diagnostics?.backup?.status==='ok'?'正常':'未配置备份'}</Badge></div><p className="muted-copy">最近 30 次执行的排队、提交和确认耗时。这里仅用于定位问题，不会影响任务调度。</p></Card><Card className="runs-card"><div className="run-list">{diagnostics?.items?.length?diagnostics.items.map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='success'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.name}</b><small>{item.stage||'—'} · 尝试 {item.attempt||1}</small></div><Badge tone={item.status==='success'?'good':item.status==='failed'?'danger':'warn'}>{item.status}</Badge><span className="run-error">{item.error_summary||`总耗时 ${item.total??'—'} 秒`}</span></div>):<Empty title="暂无诊断记录"/>}</div></Card></>}
    {section==='customers'&&<Card><div className="section-head"><h2>客户详情</h2><Link to="/admin/users">返回用户管理 ↗</Link></div><p className="muted-copy">客户详情已统一到 React 管理工作区。请选择用户后查看账号、订阅和任务。</p></Card>}
    {section==='settings'&&!isMail&&<><Card><div className="section-head"><div><span className="eyebrow">RELEASE CHANNEL</span><h2>版本更新</h2></div><Badge tone={update?.online?'good':'neutral'}>{update?.current?`当前 ${update.current}`:'读取中'}</Badge></div><p className="muted-copy">仅 Web 更新不会停止抖音登录、发送和监控服务；涉及后端或数据结构的版本请使用完整更新。</p>{update?.configured?<div className="setting-actions"><Button variant="quiet" onClick={()=>api('/api/admin/updates/check',{method:'POST',headers:{'X-CSRF-Token':data.csrf},body:JSON.stringify({})}).then(()=>setFeedback('已请求检查更新')).catch(e=>setFeedback(e.message))}>检查更新</Button>{update.available&&<><Button variant="quiet" onClick={()=>api('/api/admin/updates/install-web',{method:'POST',headers:{'X-CSRF-Token':data.csrf,'Content-Type':'application/json'},body:JSON.stringify({version:update.available})}).then(()=>setFeedback('Web 更新已排队，发送服务不会重启')).catch(e=>setFeedback(e.message))}>仅更新 Web {update.available}</Button><Button onClick={()=>api('/api/admin/updates/install',{method:'POST',headers:{'X-CSRF-Token':data.csrf,'Content-Type':'application/json'},body:JSON.stringify({version:update.available})}).then(()=>setFeedback('完整更新已排队')).catch(e=>setFeedback(e.message))}>完整更新</Button></>}</div>:<Badge tone="warn">尚未配置服务器更新通道</Badge>}</Card><Card><div className="section-head"><div><span className="eyebrow">MAIL DELIVERY</span><h2>邮件服务</h2></div><Badge tone={data.mail?.enabled?'good':'neutral'}>{data.mail?.enabled?'已启用':'未启用'}</Badge></div><p className="muted-copy">发信配置保存在服务端密文中，密码不会回显。完整 SMTP 配置可在保存后测试连接。</p><div className="quick-links"><Link to="/notifications">打开用户通知设置 ↗</Link><Link to="/admin/settings/mail">打开完整邮件设置 ↗</Link></div></Card></>}
    {isMail&&<MailAdmin data={data} onSaved={()=>{setFeedback('邮件配置已保存');onRefresh()}}/>}
    <AdminTaskEditor task={selectedTask} csrf={data.csrf} onClose={()=>setSelectedTask(null)} onSaved={()=>{setSelectedTask(null);setFeedback('任务已保存');onRefresh()}}/>
    <AdminUserWorkspace workspace={userWorkspace} csrf={data.csrf} onClose={()=>setUserWorkspace(null)} onRefresh={refreshWorkspace} onFeedback={setFeedback} onTaskEdit={task=>setSelectedTask({...task,username:userWorkspace?.user?.username,account:userWorkspace?.accounts?.find(a=>a.id===task.accountId)?.name,account_id:task.accountId,target_sec_uid:task.sec_uid,message_kind:task.kind==='续火花表情'?'douyin_sticker':'text'})}/>
  </>;
}
function MailAdmin({data,onSaved}) {
  const config=data.mail||{};
  const [form,setForm]=useState({host:config.host||'',port:config.port||465,mode:config.mode||'ssl',username:config.username||'',password:'',sender:config.sender||'',sender_name:config.sender_name||'火花守护',enabled:!!config.enabled,events:config.events||[]});
  const [status,setStatus]=useState('');
  const change=(key,value)=>setForm(current=>({...current,[key]:value}));
  const send=async action=>{setStatus('正在处理…');try{const value=await api(`/admin/settings/mail/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form,csrf_token:data.csrf})});setStatus(value.message||'操作完成');if(action==='save')onSaved()}catch(e){setStatus(e.message)}};
  return <div className="mail-settings-grid"><Card><div className="section-head"><div><span className="eyebrow">SMTP CONFIGURATION</span><h2>发信服务</h2></div><Badge tone={form.enabled?'good':'neutral'}>{form.enabled?'已启用':'未启用'}</Badge></div><div className="form-stack"><label>SMTP 主机<input className="input" value={form.host} onChange={e=>change('host',e.target.value)} placeholder="smtp.example.com"/></label><div className="form-grid"><label>端口<input className="input" type="number" value={form.port} onChange={e=>change('port',e.target.value)}/></label><label>加密方式<select className="input" value={form.mode} onChange={e=>change('mode',e.target.value)}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option></select></label></div><label>SMTP 用户名<input className="input" value={form.username} onChange={e=>change('username',e.target.value)}/></label><label>SMTP 授权码<input className="input" type="password" value={form.password} onChange={e=>change('password',e.target.value)} placeholder={config.password_configured?'已配置，留空保持不变':'填写授权码'}/></label><div className="form-grid"><label>发件邮箱<input className="input" value={form.sender} onChange={e=>change('sender',e.target.value)}/></label><label>发件人名称<input className="input" value={form.sender_name} onChange={e=>change('sender_name',e.target.value)}/></label></div><label className="check-line"><input type="checkbox" checked={form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用邮件通知服务</label>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button onClick={()=>send('save')}>保存设置</Button><Button variant="quiet" onClick={()=>send('test')}>测试连接</Button></div></div></Card><Card><span className="eyebrow">DELIVERY LOG</span><h2>发送记录</h2><div className="run-list mail-deliveries">{(config.deliveries||[]).map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='accepted'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.event}</b><small>{item.recipient} · {item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):''}</small></div><Badge tone={item.status==='accepted'?'good':item.status==='failed'?'danger':'warn'}>{item.status}</Badge></div>)}</div></Card></div>;
}
function AdminRuns({rows=[]}) { return <div className="run-list admin-runs">{rows.length?rows.map(r=><div className="run-row" key={r.id}><span className={`run-dot ${r.tone||''}`}/><div className="grow"><b>{r.target}</b><small>{r.owner} · {r.time}</small></div><Badge tone={r.tone||'neutral'}>{r.status}</Badge></div>):<Empty title="暂无记录"/>}</div>; }
function AdminTaskEditor({task, csrf, onClose, onSaved}) {
  const [form,setForm]=useState({time:task?.time||'09:30', kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text', message:task?.message||''});
  const [status,setStatus]=useState('');
  useEffect(()=>setForm({time:task?.time||'09:30',kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text',message:task?.message||''}),[task]);
  if(!task)return null;
  const save=async e=>{e.preventDefault();setStatus('正在保存…');try{await api(`/api/admin/tasks/${task.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,account_id:task.account_id,target_name:task.name,target_sec_uid:task.target_sec_uid||'',send_time:form.time,message_kind:form.kind,message_template:form.kind==='text'?form.message:'续火花'})});setStatus('已保存');onSaved()}catch(error){setStatus(error.message)}};
  return <Drawer open={!!task} onClose={onClose} title={`编辑 ${task.name}`} eyebrow="TASK WORKBENCH" wide><div className="drawer-content"><form className="form-stack admin-editor-form" onSubmit={save}><div className="edit-target"><span className="avatar avatar-large">{task.name?.slice(0,1)}</span><div><b>{task.name}</b><small>{task.username} · {task.account||'未绑定账号'}</small></div><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'已启用':'已暂停'}</Badge></div><div className="admin-form-section"><span className="eyebrow">DELIVERY PLAN</span><label>每日发送时间<input className="input" type="time" value={form.time} onChange={e=>setForm(v=>({...v,time:e.target.value}))}/></label><label>消息类型<select className="input" value={form.kind} onChange={e=>setForm(v=>({...v,kind:e.target.value}))}><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></select></label>{form.kind==='text'&&<label>消息内容<textarea className="input textarea" value={form.message} onChange={e=>setForm(v=>({...v,message:e.target.value}))}/></label>}</div><div className="admin-locked-target"><span>目标好友</span><b>{task.name}</b><small>稳定身份已锁定，编辑不会重新选择好友</small></div>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button type="submit">保存任务</Button><Button variant="quiet" type="button" onClick={onClose}>取消</Button></div></form></div></Drawer>;
}
function AdminUserWorkspace({workspace, csrf, onClose, onRefresh, onFeedback, onTaskEdit}) {
  if(!workspace)return null;
  const user=workspace.user||{}; const membership=workspace.membership||{};
  const mutate=async(path,options={})=>{try{const value=await api(path,options);onFeedback(value?.message||'操作已完成');onRefresh()}catch(error){onFeedback(error.message)}};
  const reset=async()=>{try{const value=await api(`/api/admin/users/${user.id}/reset-password`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})});onFeedback(value?.temporary_password?`临时密码：${value.temporary_password}`:'密码已重置');onRefresh()}catch(error){onFeedback(error.message)}};
  const toggle=()=>mutate(`/api/admin/users/${user.id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})});
  const extend=()=>mutate(`/admin/users/${user.id}/subscription`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrf_token:csrf,duration_days:'30',max_accounts:String(membership.max_accounts||1),max_tasks:String(membership.max_tasks||10)})});
  const taskAction=async task=>{const enabled=!task.enabled;try{await api(`/api/admin/tasks/${task.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,enabled})});onFeedback(enabled?'任务已恢复':'任务已暂停');onRefresh()}catch(error){onFeedback(error.message)}};
  return <Drawer open={!!workspace} onClose={onClose} title={user.username} eyebrow="CUSTOMER WORKBENCH" wide><div className="drawer-content"><div className="workspace-summary"><div><h3>{user.username}</h3><p>{user.role==='admin'?'管理员':'普通用户'} · 注册于 {user.created}</p></div><Badge tone={user.status==='active'?'good':'danger'}>{user.status==='active'?'正常':'已停用'}</Badge></div><div className="metric-grid workspace-metrics"><Metric label="抖音账号" value={workspace.accounts?.length||0} detail="已绑定"/><Metric label="续火任务" value={workspace.tasks?.length||0} detail="全部任务" tone="accent"/><Metric label="执行记录" value={workspace.runs?.length||0} detail="最近记录"/><Metric label="订阅" value={membership.active?'有效':'未开通'} detail={membership.days_remaining!=null?`剩余 ${membership.days_remaining} 天`:'管理员'} /></div><section className="workspace-section-card"><div className="section-head"><h3>账号与状态</h3><span className="eyebrow">{workspace.accounts?.length||0} ACCOUNTS</span></div>{workspace.accounts?.length?workspace.accounts.map(account=><div className="admin-row compact" key={account.id}><b>{account.name}<small>{account.profile?.nickname||'抖音账号'}</small></b><Badge tone={account.validation_state==='valid'?'good':'warn'}>{account.health?.label||account.validation_state||'未知'}</Badge></div>):<Empty title="未绑定抖音账号"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>任务与好友</h3><span className="eyebrow">{workspace.tasks?.length||0} TASKS</span></div>{workspace.tasks?.length?workspace.tasks.map(task=><div className="admin-row compact" key={task.id}><b>{task.name}<small>{task.time} · {task.kind}</small></b><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'运行中':'已暂停'}</Badge><span className="row-actions"><Button variant="quiet" onClick={()=>onTaskEdit?.(task)}>编辑</Button><Button variant="quiet" onClick={()=>taskAction(task)}>{task.enabled?'暂停':'恢复'}</Button></span></div>):<Empty title="暂无续火任务"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>最近执行</h3><span className="eyebrow">{workspace.runs?.length||0} RUNS</span></div>{workspace.runs?.length?workspace.runs.slice(0,6).map(run=><div className="admin-row compact" key={run.id}><b>{run.name}<small>{run.time} · {run.error||'执行完成'}</small></b><Badge tone={run.status==='success'?'good':run.status==='failed'?'danger':'warn'}>{run.label||run.status}</Badge></div>):<Empty title="暂无执行记录"/>}</section><section className="workspace-section-card"><div className="section-head"><h3>订阅与额度</h3><span className="eyebrow">SUBSCRIPTION</span></div><div className="workspace-subscription-line"><b>{membership.status_label||'未开通'}</b><span>{membership.valid_until?new Date(membership.valid_until).toLocaleDateString('zh-CN'):'—'} · 最多 {membership.max_accounts||1} 个账号 / {membership.max_tasks||10} 个任务</span><Button variant="quiet" onClick={extend}>续期 30 天</Button></div></section><div className="setting-actions workspace-actions"><Button variant="quiet" onClick={toggle}>{user.status==='active'?'停用用户':'启用用户'}</Button><Button variant="quiet" onClick={reset}>重置密码</Button><Button variant="quiet" onClick={onClose}>关闭</Button></div></div></Drawer>;
}
function AuthPassword({name,id,label,autoComplete,required=true}) { const [shown,setShown]=useState(false); return <label className="auth-field"><span>{label}</span><span className="auth-password"><input id={id} name={name} type={shown?'text':'password'} autoComplete={autoComplete} required={required}/><button type="button" className="auth-password-toggle" onClick={()=>setShown(v=>!v)} aria-label={shown?'隐藏密码':'显示密码'}>{shown?'隐藏':'显示'}</button></span></label>; }
function AuthPage({kind}) {
  const params=new URLSearchParams(window.location.search); const error=params.get('error'); const username=params.get('username')||''; const registered=params.get('registered')==='1'; const sent=params.get('sent')==='1';
  const [csrf,setCsrf]=useState('');
  useEffect(()=>{if(kind==='change')api('/api/auth/bootstrap').then(v=>{const token=v?.csrf||'';setCsrf(token);const form=document.querySelector('.auth-form');if(form){let field=form.querySelector('[name="csrf_token"]');if(!field){field=document.createElement('input');field.type='hidden';field.name='csrf_token';form.prepend(field)}field.value=token}}).catch(()=>{})},[kind]);
  const title=kind==='login'?'欢迎回来':kind==='register'?'创建账号':kind==='forgot'?'找回网站密码':'设置登录密码';
  const action=kind==='login'?'/login':kind==='register'?'/register':kind==='forgot'?(sent?'/forgot-password/reset':'/forgot-password'):'/change-password';
  return <main className="auth-shell"><aside className="auth-art"><span className="eyebrow">KEEP THE CONNECTION. / 每天，都在。</span><div className="auth-art-title">不让默契<br/>停在昨天<span>.</span></div><div className="auth-art-mark">✦<small>STAY<br/>IN TOUCH.</small></div><p>把日常交给火花。<br/>把时间留给在意的人。</p></aside><section className="auth-panel"><nav className="auth-tabs" aria-label="账户入口"><a className={kind==='login'?'active':''} href="/login">登录</a><a className={kind==='register'?'active':''} href="/register">注册</a></nav><div className="auth-heading"><span className="eyebrow">DYFIRE / ACCOUNT</span><h1>{title}</h1><p>{kind==='login'?'登录后管理抖音账号与续火任务。':kind==='register'?'邀请码可选，开通订阅后即可绑定抖音账号。':kind==='forgot'?'使用已验证邮箱找回网站密码。':'设置一个新的平台登录密码。'}</p></div>{registered&&<div className="auth-notice good" role="status">注册成功，请登录后兑换会员时长。</div>}{sent&&<div className="auth-notice" role="status">如果账号和已验证邮箱匹配，重置邮件会进入发送队列。</div>}{error&&<div className="auth-notice danger" role="alert">{error}</div>}<form className="auth-form" method="post" action={action}>{kind==='login'&&<><label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" placeholder="输入用户名或邮箱" required autoFocus/></label><AuthPassword name="password" id="auth-login-password" label="密码" autoComplete="current-password"/></>}{kind==='register'&&<><label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" minLength="3" placeholder="设置用户名或邮箱" required autoFocus/><small>3–32 位字母、数字、@、点、下划线或短横线</small></label><AuthPassword name="password" id="auth-register-password" label="密码" autoComplete="new-password"/><AuthPassword name="password_confirmation" id="auth-register-password-confirmation" label="确认密码" autoComplete="new-password"/><label className="auth-field"><span>注册邀请码（选填）</span><input name="invite_code" autoComplete="off" placeholder="有邀请码时填写"/></label></>}{kind==='forgot'&&!sent&&<><label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} autoComplete="username" required/></label><label className="auth-field"><span>已验证的接收邮箱</span><input name="email" type="email" autoComplete="email" required/></label></>}{kind==='forgot'&&sent&&<><label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} required/></label><label className="auth-field"><span>重置代码</span><input name="code" inputMode="text" autoComplete="one-time-code" maxLength="8" required/></label><AuthPassword name="password" id="auth-reset-password" label="新密码" autoComplete="new-password"/><AuthPassword name="confirmation" id="auth-reset-confirmation" label="再次输入新密码" autoComplete="new-password"/></>}{kind==='change'&&<><AuthPassword name="current_password" id="auth-current-password" label="当前密码" autoComplete="current-password"/><AuthPassword name="new_password" id="auth-new-password" label="新密码" autoComplete="new-password"/><AuthPassword name="new_password_confirmation" id="auth-new-password-confirmation" label="确认新密码" autoComplete="new-password"/></>}<button className="auth-submit" type="submit">{kind==='login'?'登录':kind==='register'?'注册并返回登录':kind==='forgot'?(sent?'重置密码':'发送重置邮件'):'保存新密码'} <span>↗</span></button></form><footer className="auth-footer">{kind==='login'?<><a href="/forgot-password">忘记网站密码？</a><span>还没有账号？<a href="/register">立即注册</a></span></>:<a href="/login">返回登录</a>}<small>请妥善保管平台密码和抖音登录信息。</small></footer></section></main>;
}
function App() {
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
  else if(path==='/dashboard'||path==='/')page=<Dashboard data={data}/>;
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
