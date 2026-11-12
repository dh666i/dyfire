import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Link,useLocation,useNavigate} from 'react-router-dom';
import {AnimatePresence} from 'motion/react';
import {Button,StatusIcon,LiveNumber} from './ui-controls.jsx';
import {Drawer} from './ui-dialog.jsx';
import {Toast} from './ui-feedback.jsx';
import {MotionList} from './workflow-motion.jsx';
import {AnimatedDetails,TabTransitionPanel,useViewportReveal} from './ui-motion.jsx';
import {FilterSummary,ListSkeleton,ReadStatus} from './ui-continuity.jsx';
import ResourceHistory from './resource-history.jsx';
import UpdateReleasePanel from './update-panel.jsx';
import DeliveryTrend from './delivery-trend.jsx';
import RowActions from './row-actions.jsx';
import RubberSegment from './components/RubberSegment';

const EMPTY_MAIL_CONFIG=Object.freeze({});

// Only the administrator route requests this module. Shared auth/bootstrap
// stay in the application shell; all mutation contracts stay unchanged.
export function createAdminPages({AccountIncidents,asList,initial,redemptionPlaintext,formatAdminDate,pendingMembershipPlan,membershipTaskLimitLabel,membershipOverLimit,membershipDate,UNLIMITED_TASKS,planDurationDays,planMaxTasks,planDurationLabel,planTaskLabel,api,AdminBusinessPanel,useDebounced,Badge,Card,Empty,confirmModal,LoadingState,PageHeader,Metric,MembershipPlanNotice,SupportReference}) {
function Admin({data,onRefresh}) {
  const location=useLocation();
  const navigate=useNavigate();
  const path=location.pathname;
  const isTaskGroup=path.startsWith('/admin/tasks/groups/');
  const rawSection=isTaskGroup?'tasks':path.split('/')[2]||'overview';
  // The old business route remains an alias so existing bookmarks keep
  // working, while the only visible operations view is service announcements.
  const baseSection=isTaskGroup||['subscriptions','tasks','customers'].includes(rawSection)?'users':rawSection==='today'?'runs':['diagnostics','audit','business','announcements'].includes(rawSection)?'operations':rawSection;
  const isMail=baseSection==='settings' && path.endsWith('/mail');
  const groupParts=isTaskGroup?path.split('/').slice(-2):[];
  const requestedView=new URLSearchParams(location.search).get('view');
  const normalizedOperationsView=requestedView==='business'?'announcements':requestedView;
  const [query,setQuery]=useState('');
  const [users,setUsers]=useState(data.users||[]);
  const [auditRows,setAuditRows]=useState(data.audit||[]);
  const [insights,setInsights]=useState(null);
  const [insightRead,setInsightRead]=useState({loading:true,error:''});
  const [adminRead,setAdminRead]=useState({pending:0,error:'',updated:null});
  const [readRevision,setReadRevision]=useState(0);
  const [selectedAudit,setSelectedAudit]=useState(null);
  const [userStatusFilter,setUserStatusFilter]=useState('');
  const [auditQuery,setAuditQuery]=useState('');
  const [insightDays,setInsightDays]=useState(7);
  const [usersPagination,setUsersPagination]=useState(null);
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
  const [operationsTab,setOperationsTab]=useState(rawSection==='diagnostics'?'diagnostics':rawSection==='audit'?'audit':['business','announcements'].includes(rawSection)?'announcements':['diagnostics','audit','announcements'].includes(normalizedOperationsView)?normalizedOperationsView:'server');
  const [selectedUsers,setSelectedUsers]=useState(new Set());
  const [selectedTasks,setSelectedTasks]=useState(new Set());
  const [update,setUpdate]=useState(null);
  const [diagnostics,setDiagnostics]=useState(null);
  const [groupDetail,setGroupDetail]=useState(null);
  const [selectedTask,setSelectedTask]=useState(null);
  const [userWorkspace,setUserWorkspace]=useState(null);
  const workspaceRead=useRef({generation:0,controller:null,userId:null});
  const [feedback,setFeedback]=useState(''); const [adminToast,setAdminToast]=useState('');
  const [liveOperations,setLiveOperations]=useState(data.operations||{});
  const [mailConfig,setMailConfig]=useState(data.mail||{});
  const [operationsHistory,setOperationsHistory]=useState(null);
  const [generatedCodes,setGeneratedCodes]=useState([]);
  const [copiedCode,setCopiedCode]=useState('');
  const [codePlanId,setCodePlanId]=useState('monthly');
  const [codeDuration,setCodeDuration]=useState(30);
  const [codeTasks,setCodeTasks]=useState(UNLIMITED_TASKS);
  const [codeAccounts,setCodeAccounts]=useState(1);
  const [codeQuantity,setCodeQuantity]=useState(1);
  const [redemptionCodes,setRedemptionCodes]=useState(data.redemptionCodes||[]);
  const [redemptionPagination,setRedemptionPagination]=useState(null);
  const [redemptionQuery,setRedemptionQuery]=useState('');
  const [redemptionStatus,setRedemptionStatus]=useState('');
  const [redemptionPage,setRedemptionPage]=useState(1);
  const settledQuery=useDebounced(query);
  const settledAuditQuery=useDebounced(auditQuery);
  const settledRunQuery=useDebounced(runQuery);
  const settledRedemptionQuery=useDebounced(redemptionQuery);
  const codePlan=(data.plans||[]).find(plan=>plan.id===codePlanId)||null;
  const codeIsCustom=!codePlan;
  // 用户管理以客户为中心展示。旧的 /admin/subscriptions、/admin/tasks 路径仍然
  // 保留给书签和后端兼容，但它们现在都落到同一张用户总览表，不再拆分成多个工作台。
  const section=baseSection==='users'?(isTaskGroup?'task-group':'users'):baseSection==='runs'?(runDay==='today'?'today':'runs'):baseSection==='operations'?(operationsTab==='server'?'operations':operationsTab):baseSection;
  useEffect(()=>{
    if(rawSection==='today')setRunDay('today');
    else if(rawSection==='runs')setRunDay(requestedView==='today'?'today':'all');
    if(rawSection==='diagnostics')setOperationsTab('diagnostics');
    else if(rawSection==='audit')setOperationsTab('audit');
    else if(['business','announcements'].includes(rawSection))setOperationsTab('announcements');
    else if(rawSection==='operations')setOperationsTab(['diagnostics','audit','announcements'].includes(normalizedOperationsView)?normalizedOperationsView:'server');
  },[rawSection,normalizedOperationsView]);
  useEffect(()=>{
    const controller=new AbortController();
    setAdminRead(current=>({...current,pending:0,error:''}));
    const readApi=async(path,options={})=>{
      setAdminRead(current=>({...current,pending:current.pending+1}));
      try{
        const result=await api(path,{...options,signal:controller.signal});
        if(controller.signal.aborted)throw new DOMException('Aborted','AbortError');
        setAdminRead(current=>({...current,updated:Date.now()}));return result;
      }catch(error){if(error.name!=='AbortError')setAdminRead(current=>({...current,error:error.message}));throw error}
      finally{if(!controller.signal.aborted)setAdminRead(current=>({...current,pending:Math.max(0,current.pending-1)}))}
    };
    if(baseSection==='users'&&!isTaskGroup) readApi(`/api/admin/bootstrap?q=${encodeURIComponent(settledQuery)}&status=${encodeURIComponent(userStatusFilter)}&page=${usersPagination?.page||1}`).then(v=>{setUsers(v.users||[]);setUsersPagination(v.usersPagination||null)}).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    if(baseSection==='operations'&&operationsTab==='audit') readApi(`/api/admin/audit?q=${encodeURIComponent(settledAuditQuery)}&page=${auditPagination?.page||1}`).then(v=>{setAuditRows(v.items||[]);setAuditPagination(v.pagination||null)}).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    if(baseSection==='codes') readApi(`/api/admin/redemption-codes?q=${encodeURIComponent(settledRedemptionQuery)}&status=${encodeURIComponent(redemptionStatus)}&page=${redemptionPage}`).then(v=>{setRedemptionCodes(v.items||[]);setRedemptionPagination(v.pagination||null)}).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    if(baseSection==='runs') readApi(`/api/admin/runs?q=${encodeURIComponent(settledRunQuery)}&status=${encodeURIComponent(runStatusFilter)}&start=${runDay==='range'?runStartDate:''}&end=${runDay==='range'?runEndDate:''}&day=${runDay==='today'?'today':''}&page=${runPage}`).then(v=>{setRunRows(v.items||[]);setRunPagination(v.pagination||null);setSendBoard(runDay==='today'?v.today_board||null:null)}).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    if(baseSection==='overview'||baseSection==='runs'){
      setInsightRead({loading:true,error:''});
      readApi(`/api/admin/insights?days=${insightDays}`).then(value=>{
        setInsights(value);setInsightRead({loading:false,error:''});
      }).catch(error=>{if(error.name!=='AbortError'){setInsightRead({loading:false,error:error.message});setFeedback(error.message)}});
    }
    if(baseSection==='operations'&&operationsTab==='diagnostics') readApi('/api/admin/diagnostics').then(setDiagnostics).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    if(baseSection==='operations'&&operationsTab==='server'){
      let active=true;
      const read=()=>{if(document.visibilityState==='hidden')return;return readApi('/admin/operations/snapshot').then(value=>{if(active&&value)setLiveOperations(value)}).catch(e=>{if(active&&e.name!=='AbortError')setFeedback(e.message)});};
      const readHistory=()=>{if(document.visibilityState==='hidden')return;return readApi('/admin/operations/history?hours=24').then(value=>{if(active&&value)setOperationsHistory(value)}).catch(e=>{if(active&&e.name!=='AbortError')setOperationsHistory(current=>({...current,state:'unavailable'}))});};
      read();
      readHistory();
      const timer=window.setInterval(read,30000);
      const historyTimer=window.setInterval(readHistory,60000);
      return()=>{active=false;controller.abort();window.clearInterval(timer);window.clearInterval(historyTimer)};
    }
    if(isTaskGroup && groupParts.length===2) readApi(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(settledQuery)}&page=${groupTasksPage}`).then(setGroupDetail).catch(e=>{if(e.name!=='AbortError')setFeedback(e.message)});
    return()=>controller.abort();
  },[settledQuery,userStatusFilter,settledAuditQuery,insightDays,usersPagination?.page,auditPagination?.page,groupTasksPage,settledRunQuery,runStatusFilter,runStartDate,runEndDate,runPage,runDay,operationsTab,baseSection,section,isMail,isTaskGroup,path,settledRedemptionQuery,redemptionStatus,redemptionPage,readRevision,data]);
  useEffect(()=>{
    if(section!=='settings'||isMail)return;
    let active=true;
    const read=()=>{if(document.visibilityState==='hidden')return;return api('/api/admin/updates').then(value=>{if(active)setUpdate(value)}).catch(error=>{if(active)setFeedback(error.message)})};
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
  useEffect(()=>{setSelectedTasks(new Set())},[query,path]);
  const title={overview:'业务概览',users:'用户管理',subscriptions:'用户管理',tasks:'用户管理','task-group':'用户管理',customers:'用户管理',codes:'兑换码',runs:'执行记录',today:'执行记录',operations:'运维监控',diagnostics:'运维监控',audit:'运维监控',announcements:'服务公告',settings:'系统设置'}[section]||'管理后台';
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
    try { const result=await api(path,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body}); if(result?.codes?.length)setGeneratedCodes(result.codes.map(item=>redemptionPlaintext(item)).filter(Boolean)); (result?.codes?.length?setAdminToast('兑换码已生成'):setAdminToast('已保存')); setFeedback('');onRefresh(); }
    catch(e){ setFeedback(e.message); }
  };
  const createCodes=async()=>{setFeedback('正在生成兑换码…');try{const result=await api('/api/admin/redemption-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,plan_id:codePlan?.id||'',duration_days:Number(codeIsCustom?codeDuration:planDurationDays(codePlan)),max_tasks:Number(codeIsCustom?codeTasks:planMaxTasks(codePlan)),max_accounts:Number(codeIsCustom?codeAccounts:(codePlan?.max_accounts||1)),quantity:Number(codeQuantity||1)})});const codes=(result.codes||[]).map(item=>redemptionPlaintext(item)).filter(Boolean);setGeneratedCodes(codes);setAdminToast(codes.length?'兑换码已生成':'生成完成');setFeedback('');onRefresh()}catch(e){setFeedback(e.message)}};
  const cancelWorkspaceRead=()=>{
    const request=workspaceRead.current;
    request.generation++;request.controller?.abort();request.controller=null;request.userId=null;
  };
  const closeWorkspace=()=>{cancelWorkspaceRead();setUserWorkspace(null)};
  const readWorkspace=async(userId,{initialTab='overview',refresh=false}={})=>{
    const request=workspaceRead.current;
    // A saved mutation for a previous customer must never reopen their drawer.
    if(refresh&&request.userId!==userId)return null;
    request.controller?.abort();
    const controller=new AbortController(), generation=++request.generation;
    request.controller=controller;request.userId=userId;
    const isCurrent=()=>request.generation===generation&&request.userId===userId&&!controller.signal.aborted;
    try{
      const value=await api(`/api/admin/users/${userId}/workspace`,{signal:controller.signal});
      if(!isCurrent())return null;
      setUserWorkspace(previous=>{
        if(!isCurrent()||(refresh&&previous?.user?.id!==userId))return previous;
        return {...value,initialTab:refresh?previous?.initialTab:initialTab};
      });
      return value;
    }catch(error){if(!isCurrent()||error.name==='AbortError')return null;throw error}
    finally{if(isCurrent())request.controller=null}
  };
  const openUser=async(user,initialTab='overview')=>{
    if(!user?.id)return;
    setUserWorkspace(null);
    try{return await readWorkspace(user.id,{initialTab})}catch(error){setFeedback(error.message)}
  };
  useEffect(()=>{
    closeWorkspace();
    const match=path.match(/^\/admin\/customers\/([^/]+)$/);
    if(section==='users'&&match)void openUser({id:match[1]});
    return cancelWorkspaceRead;
  },[path,section]);
  const refreshWorkspace=async()=>{
    const userId=userWorkspace?.user?.id;
    if(!userId)return;
    const value=await readWorkspace(userId,{refresh:true});
    if(value&&workspaceRead.current.userId===userId)await refreshUsers();
  };
  // Mutations invalidate the current view. The effect above owns cancellation
  // and reads the latest filters instead of the mutation's captured filters.
  const refreshUsers=()=>setReadRevision(value=>value+1);
  const retryRun=async id=>{try{await api(`/api/admin/runs/${id}/retry`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})});setAdminToast('已安排补发，请查看后续执行记录');setReadRevision(value=>value+1);onRefresh()}catch(error){setFeedback(error.message)}};
  const toggleSelected=(setter,id)=>setter(current=>{const next=new Set(current);next.has(id)?next.delete(id):next.add(id);return next});
  const runBulk=async(path,body,success)=>{setFeedback('');try{const result=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,...body})});setAdminToast(`${success}（${result.updated||0} 项）`);setSelectedUsers(new Set());setSelectedTasks(new Set());onRefresh();if(baseSection==='users'&&!isTaskGroup)refreshUsers();if(isTaskGroup&&groupParts.length===2)api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}`).then(setGroupDetail);}catch(error){setFeedback(error.message)}};
  const adminHeader=<PageHeader title={title} actions={<Button variant="quiet" onClick={async()=>{const result=await onRefresh();if(!result)setReadRevision(value=>value+1)}}>刷新数据</Button>}/>;
  const runsTabItems=[{value:'today',label:'今日'},{value:'all',label:'全部记录'}];
  const opsTabItems=[{value:'server',label:'服务器'},{value:'diagnostics',label:'执行诊断'},{value:'audit',label:'审计日志'},{value:'announcements',label:'服务公告'}];
  return <>{adminHeader}{feedback&&<div className="inline-feedback danger-text" role="alert">{feedback}</div>}<Toast message={adminToast} onClose={()=>setAdminToast('')}/>
    {baseSection==='runs'&&<div className="admin-segment-wrap"><RubberSegment className="admin-segment" items={runsTabItems} value={runDay==='today'?'today':'all'} onChange={tab=>{setRunPage(1);setRunDay(tab==='today'?'today':'all');navigate(`/admin/runs?view=${tab}`)}} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="md" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="执行记录视图"/></div>}
    {baseSection==='operations'&&<div className="admin-segment-wrap"><RubberSegment className="admin-segment" items={opsTabItems} value={operationsTab} onChange={tab=>{setOperationsTab(tab);navigate(`/admin/operations?view=${tab}`)}} trackColor="rgba(255,255,255,.035)" thumbColor="#ff6b25" textColor="#a88f83" activeTextColor="#20100a" size="md" radius={999} inset={3} equalSlots={false} draggable={false} aria-label="运维工作台视图"/></div>}
    {section==='overview'&&<><div className="metric-grid admin-metrics"><Metric label="用户" value={counts.users||0} detail="注册账户"/><Metric label="抖音账号" value={counts.accounts||0} detail="绑定数量"/><Metric label="续火任务" value={counts.tasks||0} detail="全部用户" tone="accent"/><Metric label="执行记录" value={counts.runs||0} detail="累计记录"/></div><AdminInsights insights={insights} loading={insightRead.loading} error={insightRead.error} days={insightDays} onDaysChange={setInsightDays} onOpenUser={openUser}/><Card><div className="section-head"><h2>最近执行</h2><Link to="/admin/runs" className="text-action">查看全部<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div><AdminRuns rows={data.recentRuns}/></Card></>}

    {section==='users'&&<Card className="admin-customer-workbench"><div className="section-head"><div><h2>全部用户</h2></div><span className="muted-copy">{usersPagination?.total??users.length} 位用户</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{if(usersPagination?.page!==1)setUsersPagination({...usersPagination,page:1});setQuery(e.target.value)}} placeholder="搜索用户名或备注"/><select className="input compact-search" aria-label="用户状态筛选" value={userStatusFilter} onChange={e=>{if(usersPagination?.page!==1)setUsersPagination({...usersPagination,page:1});setUserStatusFilter(e.target.value)}}><option value="">全部状态</option><option value="active">正常</option><option value="disabled">已停用</option></select><span className="muted-copy">本页已选 {selectedUsers.size}</span></div><FilterSummary items={[{key:'q',label:'用户',value:query,onRemove:()=>{setQuery('');setUsersPagination(p=>({...p,page:1}))}},{key:'status',label:'状态',value:userStatusFilter==='active'?'正常':userStatusFilter==='disabled'?'已停用':'',onRemove:()=>{setUserStatusFilter('');setUsersPagination(p=>({...p,page:1}))}}]} onClear={()=>{setQuery('');setUserStatusFilter('');setUsersPagination(p=>({...p,page:1}))}}/>{(adminRead.pending>0||adminRead.error)&&<ReadStatus busy={adminRead.pending>0} error={adminRead.error}/>} {!adminRead.pending&&!users.length&&<Empty title="没有匹配的用户" text="调整搜索内容或清除当前筛选。"/>}{selectedUsers.size>0&&<div className="admin-bulk-bar"><span>已选择 {selectedUsers.size} 位客户</span><Button variant="quiet" onClick={()=>runBulk('/api/admin/users/bulk',{user_ids:[...selectedUsers],action:'enable'},'已启用')}>批量启用</Button><Button variant="danger" onClick={()=>runBulk('/api/admin/users/bulk',{user_ids:[...selectedUsers],action:'disable'},'已停用')}>批量停用</Button><Button variant="quiet" onClick={()=>setSelectedUsers(new Set())}>清除选择</Button></div>}<div className="admin-table"><div className="table-head user-workbench-head"><span>选择</span><span>客户</span><span>订阅</span><span>资源使用</span><span>账号 / 今日</span><span>操作</span></div><MotionList className="admin-users-motion-list">{users.map(u=>{const pending=pendingMembershipPlan(u);const overLimit=membershipOverLimit(u);const membershipStatus=u.membership_status||'inactive';const membershipTone=membershipStatus==='active'||membershipStatus==='admin'?'good':membershipStatus==='expiring'?'warn':'neutral';return <div className="admin-row admin-user-row user-workbench-row" data-motion-state={`${u.status}-${u.account_health_status}`} key={u.id}><input type="checkbox" className="row-check" checked={selectedUsers.has(u.id)} disabled={u.role==='admin'||u.id===data.user?.id} onChange={()=>toggleSelected(setSelectedUsers,u.id)} aria-label={`选择 ${u.username}`}/><button className="admin-cell-button row-primary" onClick={()=>openUser(u)}><b>{u.username}{u.admin_note&&<small className="admin-user-note" title={u.admin_note}>{u.admin_note}</small>}<small>{u.role==='admin'?'管理员':'普通用户'} · 注册于 {u.created}</small></b></button><span className="row-status"><Badge tone={membershipTone}>{u.membership_label||'未开通'}</Badge><small>{u.valid_until?`至 ${new Date(u.valid_until).toLocaleDateString('zh-CN')}`:'可直接绑定账号'}</small>{pending&&<small className="admin-pending-plan">下期 {membershipTaskLimitLabel(pending)} · {membershipDate(pending.valid_from||pending.starts_at||pending.effective_at)}</small>}</span><span className="row-secondary">{u.enabled_task_count??u.task_count??0} / {u.max_tasks>=UNLIMITED_TASKS||u.max_tasks==null?'不限':u.max_tasks} 个启用任务<small>{u.account_count??0} 个账号 · {u.task_count??0} 个任务</small>{overLimit>0&&<small className="admin-over-limit">超出 {overLimit} 个启用任务</small>}</span><span className="admin-user-health"><Badge tone={u.status!=='active'?'danger':(u.account_health_status==='需处理'?'warn':'good')}>{u.status!=='active'?'用户停用':(u.account_health_label||'账号正常')}</Badge><small>今日成功 {u.today_success||0} · 失败 {u.today_failed||0}</small></span><span className="row-actions row-action-cell"><Button variant="quiet" onClick={()=>openUser(u,'manage')}>编辑</Button><RowActions label={`${u.username} 更多操作`} items={[{label:'订阅与额度',disabled:u.role==='admin',onClick:()=>openUser(u,'manage')},{label:u.status==='active'?'停用用户':'启用用户',danger:u.status==='active',disabled:u.role==='admin'||u.id===data.user?.id,onClick:async()=>{try{await api(`/api/admin/users/${u.id}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})});setAdminToast('用户状态已更新');refreshUsers();onRefresh()}catch(e){setFeedback(e.message)}}}]}/></span></div>})}</MotionList></div><AdminPagination value={usersPagination} onChange={page=>setUsersPagination(p=>({...p,page}))}/></Card>}
    {section==='codes'&&<>
      <Card><div className="section-head"><div><h2>生成兑换码</h2><p className="muted-copy">未兑换的兑换码不会过期，兑换后从兑换时间开始计算有效期。</p></div></div><div className="form-grid admin-code-form"><label>套餐<select className="input" id="code-plan" value={codePlanId} onChange={e=>{const id=e.target.value;setCodePlanId(id);const plan=(data.plans||[]).find(item=>item.id===id);if(plan){setCodeDuration(planDurationDays(plan));setCodeTasks(planMaxTasks(plan));setCodeAccounts(plan.max_accounts||1)}else{setCodeDuration(30);setCodeTasks(UNLIMITED_TASKS);setCodeAccounts(1)}}}>{(data.plans||[]).map(plan=><option value={plan.id} key={plan.id}>{plan.name} · ¥{plan.price_yuan} / {planDurationLabel(plan)} · {planTaskLabel(plan)}</option>)}<option value="">自定义额度</option></select></label>{codeIsCustom&&<><label>有效天数<input className="input" id="code-days" type="number" min="1" max="36500" value={codeDuration} onChange={e=>setCodeDuration(e.target.value)}/></label><label>任务额度<input className="input" id="code-tasks" type="number" min="1" max={UNLIMITED_TASKS} value={codeTasks} onChange={e=>setCodeTasks(e.target.value)}/></label><label>账号额度<input className="input" id="code-accounts" type="number" min="1" max="100" value={codeAccounts} onChange={e=>setCodeAccounts(e.target.value)}/></label></>}<label>数量<input className="input" id="code-quantity" type="number" min="1" max="1000" value={codeQuantity} onChange={e=>setCodeQuantity(e.target.value)}/></label></div><div className="muted-copy admin-code-selection">{codePlan?`${codePlan.name}：${planDurationLabel(codePlan)} · ${planTaskLabel(codePlan)}`:'自定义额度：请确认有效天数和任务额度'}</div><Button onClick={createCodes}>生成兑换码</Button></Card>
      {generatedCodes.length>0&&<Card className="redemption-generated-card"><div className="section-head"><div><h2>刚生成的兑换码</h2></div><div className="redemption-generated-actions"><Badge tone="good">{generatedCodes.length} 个</Badge><Button variant="quiet" onClick={copyAllRedemptionCodes}>{copiedCode==='__all__'?'已复制全部':'复制全部'}</Button></div></div><div className="redemption-generated-list">{generatedCodes.map(code=><div className="redemption-generated-row" key={code}><code>{code}</code><Button variant="quiet" onClick={()=>copyRedemptionCode(code)}>{copiedCode===code?'已复制':'复制'}</Button></div>)}</div></Card>}
      <Card className="redemption-table-card"><div className="section-head"><h2>兑换码记录</h2><span className="muted-copy">{redemptionPagination?.total??redemptionCodes.length} 个兑换码</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={redemptionQuery} onChange={e=>{setRedemptionPage(1);setRedemptionQuery(e.target.value)}} placeholder="输入完整兑换码、编号或用户"/><select className="input compact-search" aria-label="兑换码状态筛选" value={redemptionStatus} onChange={e=>{setRedemptionPage(1);setRedemptionStatus(e.target.value)}}><option value="">全部状态</option><option value="active">未使用</option><option value="redeemed">已兑换</option><option value="revoked">已撤销</option><option value="expired">已过期</option></select></div><FilterSummary items={[{key:'q',label:'兑换码',value:redemptionQuery,onRemove:()=>{setRedemptionQuery('');setRedemptionPage(1)}},{key:'state',label:'状态',value:({active:'未使用',redeemed:'已兑换',revoked:'已撤销',expired:'已过期'})[redemptionStatus]||'',onRemove:()=>{setRedemptionStatus('');setRedemptionPage(1)}}]} onClear={()=>{setRedemptionQuery('');setRedemptionStatus('');setRedemptionPage(1)}}/><div className="admin-table redemption-table"><div className="table-head"><span>兑换码</span><span>状态 / 使用者</span><span>兑换日期</span><span>有效期至</span><span>额度</span></div>{redemptionCodes.map(c=>{const code=redemptionPlaintext(c);const status=c.status||'未使用';const tone=status==='未使用'||status==='待兑换'?'good':status==='已撤销'?'danger':'neutral';return <div className="admin-row redemption-record-row" key={c.id||code}><div className="redemption-code-cell" data-label="兑换码"><code>{code||'历史码未保存'}</code>{code&&<Button variant="quiet" onClick={()=>copyRedemptionCode(code)}>{copiedCode===code?'已复制':'复制'}</Button>}</div><span data-label="状态 / 使用者"><Badge tone={tone}>{status==='待兑换'?'未使用':status}</Badge><small>{c.username||c.used_by_username||'尚未兑换'}</small></span><time data-label="兑换日期">{formatAdminDate(c.redeemed_at)}</time><time data-label="有效期至">{c.expires_at?formatAdminDate(c.expires_at):'兑换后开始计算'}</time><span data-label="额度">{c.max_accounts} 账号 · {c.task_limit_label||c.max_tasks} 任务</span></div>})}</div><AdminPagination value={redemptionPagination} onChange={page=>setRedemptionPage(page)}/></Card>
    </>}
    {section==='task-group'&&<Card><div className="section-head"><div><h2>{groupDetail?.items?.[0]?.account||'账号任务'}</h2></div><Link to="/admin/users" className="text-action"><StatusIcon kind="chevronLeft" className="navigation-chevron is-back"/>返回用户管理</Link></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={query} onChange={e=>{setGroupTasksPage(1);setQuery(e.target.value)}} placeholder="搜索好友"/><span className="muted-copy">本页已选 {selectedTasks.size}</span></div>{selectedTasks.size>0&&<div className="admin-bulk-bar"><span>已选 {selectedTasks.size} 条任务</span><Button variant="quiet" onClick={()=>runBulk('/api/admin/tasks/bulk',{task_ids:[...selectedTasks],action:'resume'},'已恢复')}>批量恢复</Button><Button variant="danger" onClick={()=>runBulk('/api/admin/tasks/bulk',{task_ids:[...selectedTasks],action:'pause'},'已暂停')}>批量暂停</Button><Button variant="quiet" onClick={()=>setSelectedTasks(new Set())}>清除选择</Button></div>}{groupDetail?.items?.length?<div className="admin-table"><div className="table-head task-detail-head"><span>选择</span><span>目标好友</span><span>发送时间</span><span>状态</span><span>操作</span></div>{groupDetail.items.map(item=><div className="admin-row task-detail-row" key={item.id}><input className="row-check" type="checkbox" checked={selectedTasks.has(item.id)} onChange={()=>toggleSelected(setSelectedTasks,item.id)} aria-label={`选择任务 ${item.name}`}/><b className="row-primary">{item.name}<small>{item.username} · {item.message_kind==='douyin_sticker'?'续火花表情':'文字消息'}</small></b><time className="row-secondary">{item.time}</time><Badge className="row-status" tone={item.enabled?'good':'neutral'}>{item.enabled?'已启用':'已暂停'}</Badge><span className="row-actions row-action-cell"><Button variant="quiet" onClick={()=>setSelectedTask(item)}>编辑</Button><Button variant="quiet" onClick={async()=>{try{await api(`/api/admin/tasks/${item.id}/enabled`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,enabled:!item.enabled})});setFeedback(item.enabled?'任务已暂停':'任务已恢复');api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}&page=${groupTasksPage}`).then(setGroupDetail);onRefresh()}catch(e){setFeedback(e.message)}}}>{item.enabled?'暂停':'恢复'}</Button><Button variant="danger" onClick={async()=>{if(await confirmModal({title:'删除续火任务',eyebrow:'TASK WORKBENCH',message:`确定删除发给 ${item.name} 的任务吗？删除后不会影响其他好友任务。`}))api(`/api/admin/tasks/${item.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})}).then(()=>{setFeedback('任务已删除');api(`/api/admin/tasks/groups/${groupParts[0]}/${groupParts[1]}?q=${encodeURIComponent(query)}&page=${groupTasksPage}`).then(setGroupDetail);onRefresh()}).catch(e=>setFeedback(e.message))}}>删除</Button></span></div>)}</div>:<Empty title="暂无任务" text={groupDetail?'这个账号下还没有匹配的续火任务。':'正在读取任务明细…'}/>}<AdminPagination value={groupDetail?.pagination} onChange={page=>setGroupTasksPage(page)}/></Card>}
    {(section==='runs'||section==='today')&&<>
      {runDay==='today'&&<TodaySendBoard board={sendBoard}/>}
      <Card><AdminRunFilters query={runQuery} onQueryChange={value=>{setRunPage(1);setRunQuery(value)}} status={runStatusFilter} onStatusChange={value=>{setRunPage(1);setRunStatusFilter(value)}} day={runDay} onDayChange={value=>{setRunPage(1);setRunDay(value)}} start={runStartDate} end={runEndDate} onStartChange={setRunStartDate} onEndChange={setRunEndDate}/><FilterSummary items={[{key:'q',label:'搜索',value:runQuery,onRemove:()=>{setRunQuery('');setRunPage(1)}},{key:'status',label:'结果',value:({success:'已发出',failed:'未发出',submitted_unconfirmed:'待核实',running:'执行中',skipped:'已跳过'})[runStatusFilter],onRemove:()=>{setRunStatusFilter('');setRunPage(1)}},{key:'date',label:'日期',value:runDay==='range'?`${runStartDate||'不限'} 至 ${runEndDate||'不限'}`:'',onRemove:()=>{setRunDay('all');setRunStartDate('');setRunEndDate('');setRunPage(1)}}]} onClear={()=>{setRunQuery('');setRunStatusFilter('');setRunStartDate('');setRunEndDate('');setRunDay('all');setRunPage(1)}}/>{(adminRead.pending>0||adminRead.error)&&<ReadStatus busy={adminRead.pending>0} error={adminRead.error}/>}<AdminRunTable rows={runRows} onRetry={retryRun} onOpenUser={openUser}/><AdminPagination value={runPagination} onChange={setRunPage}/></Card>
    </>}
    {section==='announcements'&&<React.Suspense fallback={<LoadingState/>}><AdminBusinessPanel data={data} onFeedback={setFeedback}/></React.Suspense>}
    {section==='operations'&&<><div className="metric-grid"><Metric label="CPU" value={liveOperations?.system?.cpu?.percent!=null?`${Math.round(liveOperations.system.cpu.percent)}%`:'—'} detail={liveOperations?.system?.scope_label||'服务器实时使用率'}/><Metric label="内存" value={liveOperations?.system?.memory?.percent!=null?`${Math.round(liveOperations.system.memory.percent)}%`:'—'} detail="服务器实时使用率" tone="accent"/><Metric label="磁盘" value={liveOperations?.system?.disk?.percent!=null?`${Math.round(liveOperations.system.disk.percent)}%`:'—'} detail="服务器实时使用率"/><Metric label="Worker" value={liveOperations?.services?.worker?.status==='healthy'?'运行中':liveOperations?.services?.worker?.label||'—'} detail="任务执行服务"/></div><div className="metric-grid admin-queue-metrics"><Metric label="待执行" value={liveOperations?.queue?.due_tasks??'—'} detail="当前到期任务"/><Metric label="运行中" value={liveOperations?.queue?.running_runs??'—'} detail="占用中的执行"/><Metric label="待重试" value={liveOperations?.queue?.retrying_runs??'—'} detail="延迟重试任务" tone="accent"/><Metric label="需关注" value={liveOperations?.queue?.attention_24h??'—'} detail="近 24 小时异常" tone="danger"/></div><ResourceHistory history={operationsHistory} snapshot={liveOperations}/><CapacitySummary business={liveOperations?.business}/></>}
    {section==='audit'&&<Card><div className="section-head"><h2>审计日志</h2><span className="muted-copy">{auditPagination?.total??auditRows.length} 条记录</span></div><div className="toolbar admin-toolbar"><input className="input compact-search" value={auditQuery} onChange={e=>{setAuditQuery(e.target.value);setAuditPagination(value=>({...value,page:1}))}} placeholder="搜索操作、操作者、资源、请求 ID 或 IP"/></div><div className="admin-table"><div className="table-head audit-table-head"><span>操作</span><span>操作者</span><span>资源</span><span>时间</span></div>{auditRows.map(row=><button type="button" className="admin-row audit-row" key={row.id} onClick={()=>setSelectedAudit(row)}><b className="row-primary">{row.event}<small>{row.outcome==='failure'?'失败':row.outcome==='success'?'成功':'历史记录'}{row.error_code?` · ${row.error_code}`:''}</small></b><span className="row-secondary">{row.actor||'系统'}</span><span className="row-secondary">{row.subject_type||'—'}<small>{row.subject_id||''}</small></span><time className="row-status">{row.created_at?new Date(row.created_at).toLocaleString('zh-CN'):'—'}</time></button>)}</div><AdminPagination value={auditPagination} onChange={page=>setAuditPagination(value=>({...value,page}))}/></Card>}
    {section==='diagnostics'&&<><Card><div className="section-head"><div><h2>执行诊断</h2></div><span className="muted">最近执行</span></div><p className="muted-copy">最近 30 次执行耗时。</p></Card><Card className="runs-card"><div className="run-list">{diagnostics?.items?.length?diagnostics.items.map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='success'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.name}</b><small>{item.stage||'—'} · 尝试 {item.attempt||1}</small></div><Badge tone={item.status==='success'?'good':item.status==='failed'?'danger':'warn'}>{({success:'已发出',failed:'失败',error:'失败',running:'执行中',queued:'排队中',pending:'待执行',skipped:'已跳过',submitted_unconfirmed:'待核实',retrying:'等待重试'})[item.status]||'待核实'}</Badge><span className="run-error">{item.error_summary||`总耗时 ${item.total??'—'} 秒`}</span></div>):<Empty title="暂无诊断记录"/>}</div></Card></>}
    {section==='settings'&&!isMail&&<UpdatePanel update={update} data={{...data,mail:mailConfig}} onFeedback={setFeedback}/>}
    {isMail&&<MailAdmin data={{...data,mail:mailConfig}} onSaved={saved=>{setAdminToast('邮件配置已保存');setMailConfig(current=>({...current,enabled:saved.enabled}));onRefresh()}}/>}
    <AdminTaskEditor key={selectedTask?.id||'no-task'} task={selectedTask} csrf={data.csrf} onClose={()=>setSelectedTask(null)} onSaved={()=>{setSelectedTask(null);setAdminToast('任务已保存');if(userWorkspace)refreshWorkspace().catch(e=>setFeedback(e.message));onRefresh()}}/>
    <AdminUserWorkspace key={userWorkspace?.user?.id||'no-user'} workspace={userWorkspace} plans={data.plans||[]} csrf={data.csrf} actorId={data.user?.id} onClose={closeWorkspace} onRefresh={refreshWorkspace} onTaskEdit={task=>setSelectedTask({...task,username:userWorkspace?.user?.username,account:userWorkspace?.accounts?.find(a=>a.id===task.accountId)?.name,account_id:task.accountId,target_sec_uid:task.sec_uid,message_kind:task.kind==='续火花表情'?'douyin_sticker':'text'})}/>
    <AuditDetailDrawer event={selectedAudit} onClose={()=>setSelectedAudit(null)}/>
  </>;
}
function UpdatePanel({update,data,onFeedback}) {
  return <><UpdateReleasePanel {...{update,data,onFeedback,api,Card,Badge,confirmModal}}/><Card><div className="section-head"><div><h2>邮件服务</h2></div><Badge tone={data.mail?.enabled?'good':'neutral'}>{data.mail?.enabled?'已启用':'未启用'}</Badge></div><div className="quick-links"><Link to="/notifications" className="text-action">打开用户通知设置<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link><Link to="/admin/settings/mail" className="text-action">打开完整邮件设置<StatusIcon kind="chevronRight" className="navigation-chevron"/></Link></div></Card></>;
}
function MailAdmin({data,onSaved}) {
  const config=data.mail||EMPTY_MAIL_CONFIG;
  const configForm=value=>({host:value.host||'',port:value.port||465,mode:value.mode||'ssl',username:value.username||'',password:'',sender:value.sender||'',sender_name:value.sender_name||'火花守护',enabled:!!value.enabled,events:value.events||[]});
  const [form,setForm]=useState(()=>configForm(config));
  const editedFields=useRef(new Set());
  useEffect(()=>{
    const incoming=configForm(config);
    // Settings arrive after the route mounts. Hydrate untouched fields while
    // preserving any draft the administrator has already started typing.
    setForm(current=>Object.fromEntries(Object.entries(incoming).map(([key,value])=>[key,editedFields.current.has(key)?current[key]:value])));
  },[config]);
  const [status,setStatus]=useState('');
  const change=(key,value)=>{editedFields.current.add(key);setForm(current=>({...current,[key]:value}))};
  const send=async action=>{setStatus('正在处理…');try{const value=await api(`/admin/settings/mail/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form,csrf_token:data.csrf})});setStatus(value.message||'操作完成');if(action==='save')onSaved({enabled:form.enabled})}catch(e){setStatus(e.message)}};
  return <div className="mail-settings-grid"><Card><div className="section-head"><div><h2>发信服务</h2></div><Badge tone={form.enabled?'good':'neutral'}>{form.enabled?'已启用':'未启用'}</Badge></div><div className="form-stack"><label>SMTP 主机<input className="input" value={form.host} onChange={e=>change('host',e.target.value)} placeholder="smtp.example.com"/></label><div className="form-grid"><label>端口<input className="input" type="number" value={form.port} onChange={e=>change('port',e.target.value)}/></label><label>加密方式<select className="input" value={form.mode} onChange={e=>change('mode',e.target.value)}><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option></select></label></div><label>SMTP 用户名<input className="input" value={form.username} onChange={e=>change('username',e.target.value)}/></label><label>SMTP 授权码<input className="input" type="password" value={form.password} onChange={e=>change('password',e.target.value)} placeholder={config.password_configured?'已配置，留空保持不变':'填写授权码'}/></label><div className="form-grid"><label>发件邮箱<input className="input" value={form.sender} onChange={e=>change('sender',e.target.value)}/></label><label>发件人名称<input className="input" value={form.sender_name} onChange={e=>change('sender_name',e.target.value)}/></label></div><label className="check-line"><input type="checkbox" role="switch" className="ui-switch" checked={form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用邮件通知服务</label>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button onClick={()=>send('save')}>保存设置</Button><Button variant="quiet" onClick={()=>send('test')}>测试连接</Button></div></div></Card><Card><h2>发送记录</h2><p className="muted" role="status">{config.worker?.online?'投递服务在线':'投递服务未连接'} · 待处理 {config.queue?.pending||0} 封{config.queue?.oldest_age_seconds>180?'，队列处理延迟':''}</p><div className="run-list mail-deliveries">{(config.deliveries||[]).map(item=><div className="run-row" key={item.id}><span className={`run-dot ${item.status==='accepted'?'success':item.status==='failed'?'failed':''}`}/><div className="grow"><b>{item.event}</b><small>{item.recipient} · {item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):''}</small></div><Badge tone={item.status==='accepted'?'good':item.status==='failed'?'danger':'warn'}>{item.status}</Badge></div>)}</div></Card></div>;
}
  function AdminRuns({rows=[]}) { return <div className="run-list admin-runs">{rows.length?rows.map(r=><div className="run-row" key={r.id}><span className={`run-dot ${r.tone||''}`}/><div className="grow"><b>{r.target}</b><small>{r.owner} · {r.time}</small></div><Badge tone={r.tone||'neutral'}>{r.status}</Badge></div>):<Empty title="暂无记录"/>}</div>; }
function AdminPagination({value,onChange}) { if(!value||value.pages<=1)return null; return <div className="admin-pagination"><span>共 {value.total} 条 · 第 {value.page} / {value.pages} 页</span><div><Button variant="quiet" disabled={!value.has_previous} onClick={()=>onChange(value.page-1)}>上一页</Button><Button variant="quiet" disabled={!value.has_next} onClick={()=>onChange(value.page+1)}>下一页</Button></div></div>; }
function AdminRunTable({rows,onRetry,onOpenUser}) {
  return <div className="admin-runs-ledger">{rows.length?rows.map(row=><article className="admin-run-record" key={row.id}><span className={`run-dot ${row.tone||'neutral'}`}/><div className="admin-run-main"><div className="admin-run-title"><b>{row.target}</b><Badge tone={row.tone||'neutral'}>{row.label||row.status}</Badge></div><div className="admin-run-meta"><button type="button" onClick={()=>onOpenUser?.({id:row.owner_id,username:row.owner})}>{row.owner}</button><span>{row.account}</span><time>{row.scheduled_for}</time><span>{row.stage}</span></div>{row.error&&<p className={row.status==='failed'?'run-error danger-text':'run-error'}>{row.error}</p>}{row.guidance?.text&&<p className="run-guidance-copy">{row.guidance.text}</p>}{row.support_id&&<SupportReference value={row.support_id}/>}{row.attempts?.length>0&&<AnimatedDetails className="admin-run-attempts"><summary>{row.attempts.length} 次尝试详情</summary>{row.attempts.map((attempt,index)=><div key={`${row.id}-${index}`}><span>第 {attempt.number||index+1} 次</span><span>{attempt.stage||'—'}</span><span>{attempt.reason||attempt.status||'—'}</span></div>)}</AnimatedDetails>}</div>{row.can_retry&&<Button variant="quiet" onClick={()=>onRetry(row.id)}>补发</Button>}</article>):<Empty title="没有匹配的执行记录" text="调整日期或状态筛选后再查看。"/>}</div>;
}
function TodaySendBoard({board}) {
  const totals=board?.totals||{}; const groups=asList(board?.groups);
  return <><div className="metric-grid today-send-metrics"><Metric label="今日计划" value={totals.planned||0} detail="当前启用任务"/><Metric label="已发出" value={totals.success||0} detail="执行已确认" tone="good"/><Metric label="未发出" value={totals.missed||0} detail="不代表账号失效" tone="accent"/><Metric label="待核实 / 验证" value={(totals.unconfirmed||0)+(totals.login||0)} detail={`${totals.unconfirmed||0} 待核实 · ${totals.login||0} 需验证`} tone="danger"/></div><p className="today-board-date">北京时间 {board?.day||'—'}</p><div className="today-board-groups">{groups.length?groups.map(group=><div className="today-board-account" key={group.key}><div><b>{group.account_name}</b><small>{group.username} · 计划 {group.counts?.planned||0}</small></div><span><Badge tone={group.health?.tone||'neutral'}>{group.health?.label||'账号状态未知'}</Badge><small>发出 {group.counts?.success||0} · 未发出 {group.counts?.missed||0} · 待核实 {group.counts?.unconfirmed||0}</small></span></div>):<Empty title="今日没有启用中的续火计划" text="可在用户管理的任务视图查看所有任务。"/>}</div></>;
}
function AdminRunFilters({query,onQueryChange,status,onStatusChange,day,onDayChange,start,end,onStartChange,onEndChange}) {
  return <div className="toolbar admin-run-toolbar"><input className="input compact-search" value={query} onChange={e=>onQueryChange(e.target.value)} placeholder="搜索客户、目标或错误"/><select className="input compact-search" aria-label="执行结果筛选" value={status} onChange={e=>onStatusChange(e.target.value)}><option value="">全部结果</option><option value="success">已发出</option><option value="failed">未发出</option><option value="submitted_unconfirmed">待核实</option><option value="running">执行中</option><option value="skipped">已跳过</option></select>{day==='range'&&<><input className="input compact-search" type="date" value={start} onChange={e=>onStartChange(e.target.value)} aria-label="开始日期"/><input className="input compact-search" type="date" value={end} onChange={e=>onEndChange(e.target.value)} aria-label="结束日期"/></>}<select className="input compact-search" aria-label="执行时间范围" value={day} onChange={e=>onDayChange(e.target.value)}><option value="today">今日</option><option value="all">全部时间</option><option value="range">日期范围</option></select></div>;
}
function AdminInsights({insights,loading=false,error='',days,onDaysChange,onOpenUser}) {
  const trend=asList(insights?.trend); const alerts=insights?.alerts||{};
  return <><div className="admin-insights-grid"><Card className="admin-trend-card"><div className="section-head"><div><h2>发送趋势</h2></div><select className="input compact-search" aria-label="发送趋势时间范围" value={days} onChange={e=>onDaysChange(Number(e.target.value))}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></div><DeliveryTrend trend={trend} loading={loading} error={error}/></Card><Card className="admin-alert-card"><div className="section-head"><div><h2>需要处理</h2></div><Badge tone={(alerts.accounts?.length||alerts.failed_runs?.length||alerts.expiring?.length)?'warn':'good'}>{(alerts.accounts?.length||0)+(alerts.failed_runs?.length||0)+(alerts.expiring?.length||0)} 项</Badge></div><div className="admin-alert-list">{(alerts.accounts||[]).slice(0,3).map(item=><button className="admin-alert-row" key={`account-${item.id}`} onClick={()=>onOpenUser?.({id:item.owner_id})}><span className="alert-marker danger"/><span><b>{item.owner} · {item.name}</b><small>抖音账号{item.label}，点击查看客户工作台</small></span><StatusIcon kind="chevronRight" className="navigation-chevron"/></button>)}{(alerts.failed_runs||[]).slice(0,3).map(item=><div className="admin-alert-row" key={`run-${item.id}`}><span className="alert-marker danger"/><span><b>{item.username} · {item.name}</b><small>{item.time} · {item.error||'执行失败或待核实'}</small></span><Link to={`/admin/runs`}>查看</Link></div>)}{(alerts.expiring||[]).slice(0,3).map(item=><button className="admin-alert-row" key={`subscription-${item.id}`} onClick={()=>onOpenUser?.({id:item.id})}><span className="alert-marker warn"/><span><b>{item.username} · 订阅即将到期</b><small>{item.days_remaining} 天后到期</small></span><StatusIcon kind="chevronRight" className="navigation-chevron"/></button>)}{!alerts.accounts?.length&&!alerts.failed_runs?.length&&!alerts.expiring?.length&&<div className="admin-alert-clear"><span className="live-dot"/><span>当前没有需要处理的异常</span></div>}</div></Card></div></>;
}
function AuditDetailDrawer({event,onClose}) {
  const [detail,setDetail]=useState(event||null); const [error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    setDetail(event||null);setError('');
    if(event?.id)api(`/api/admin/audit/${event.id}`,{signal:controller.signal})
      .then(value=>{if(!controller.signal.aborted)setDetail(value)})
      .catch(error=>{if(!controller.signal.aborted&&error.name!=='AbortError')setError(error.message)});
    return()=>controller.abort();
  },[event?.id]);
  return <Drawer open={!!event} onClose={onClose} title="审计详情" eyebrow="AUDIT EVENT" wide><div className="drawer-content">{detail&&<dl className="audit-detail-list"><dt>操作</dt><dd>{detail.event}</dd><dt>结果</dt><dd>{detail.outcome==='failure'?'失败':detail.outcome==='success'?'成功':'历史记录'}{detail.error_code?` · ${detail.error_code}`:''}</dd><dt>操作者</dt><dd>{detail.actor||'—'}</dd><dt>资源</dt><dd>{detail.subject_type||'—'} · {detail.subject_id||'—'}</dd><dt>请求 ID</dt><dd>{detail.request_id||'—'}</dd><dt>客户端 IP</dt><dd>{detail.client_ip||'—'}</dd><dt>时间</dt><dd>{detail.created_at?new Date(detail.created_at).toLocaleString('zh-CN'):'—'}</dd><dt>详情</dt><dd>{detail.detail||'没有额外说明'}</dd></dl>}{error&&<div className="inline-feedback danger-text">{error}</div>}</div></Drawer>;
}
function AdminTaskEditor({task, csrf, onClose, onSaved}) {
  const [form,setForm]=useState({time:task?.time||'09:30', kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text', message:task?.message||''});
  const [status,setStatus]=useState('');
  const [saving,setSaving]=useState(false);
  const saveLock=useRef(false);
  useEffect(()=>setForm({time:task?.time||'09:30',kind:task?.message_kind==='douyin_sticker'?'douyin_sticker':'text',message:task?.message||''}),[task]);
  if(!task)return null;
  const dirty=form.time!==(task.time||'09:30')||form.kind!==(task.message_kind==='douyin_sticker'?'douyin_sticker':'text')||(form.kind==='text'&&form.message!==(task.message||''));
  const diff=[];
  if(form.time!==(task.time||'09:30'))diff.push({label:'每日发送时间',from:task.time||'09:30',to:form.time});
  const oldKind=task.message_kind==='douyin_sticker'?'续火花表情':'文字消息';const newKind=form.kind==='douyin_sticker'?'续火花表情':'文字消息';
  if(oldKind!==newKind)diff.push({label:'消息类型',from:oldKind,to:newKind});
  if(form.kind==='text'&&form.message!==(task.message||''))diff.push({label:'消息内容',from:task.message||'（空）',to:form.message||'（空）'});
  const canCloseEditor=()=>!saveLock.current&&(!dirty||confirmModal({title:'放弃未保存的修改？',message:'任务更改尚未保存。',confirmLabel:'放弃修改',cancelLabel:'继续编辑'}));
  const closeEditor=async()=>{if(await canCloseEditor())onClose()};

  const save=async e=>{
    e.preventDefault();if(saveLock.current)return;
    saveLock.current=true;setSaving(true);
    try{
      if(dirty&&!await confirmModal({title:'确认保存任务修改',message:diff.map(item=>`${item.label}：${item.from} → ${item.to}`).join('\n'),confirmLabel:'确认保存'}))return;
      setStatus('正在保存…');
      await api(`/api/admin/tasks/${task.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,account_id:task.account_id,target_name:task.name,target_sec_uid:task.target_sec_uid||'',send_time:form.time,message_kind:form.kind,message_template:form.kind==='text'?form.message:'续火花'})});
      setStatus('已保存');onSaved();
    }catch(error){setStatus(error.message)}finally{saveLock.current=false;setSaving(false)}
  };
  return <Drawer open={!!task} onClose={onClose} canClose={canCloseEditor} title={`编辑 ${task.name}`} eyebrow="TASK WORKBENCH" wide><div className="drawer-content"><form className="form-stack admin-editor-form" onSubmit={save}><div className="edit-target"><span className="avatar avatar-large">{task.name?.slice(0,1)}</span><div><b>{task.name}</b><small>{task.username} · {task.account||'未绑定账号'}</small></div><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'已启用':'已暂停'}</Badge></div><div className="admin-form-section"><label>每日发送时间<input className="input" type="time" value={form.time} onChange={e=>setForm(v=>({...v,time:e.target.value}))}/></label><label>消息类型<select className="input" value={form.kind} onChange={e=>setForm(v=>({...v,kind:e.target.value}))}><option value="douyin_sticker">续火花表情</option><option value="text">文字消息</option></select></label>{form.kind==='text'&&<label>消息内容<textarea className="input textarea" value={form.message} onChange={e=>setForm(v=>({...v,message:e.target.value}))}/></label>}</div>{diff.length>0&&<div className="admin-change-preview" aria-live="polite"><b>待保存修改</b>{diff.map((item,index)=><div key={`${item.label}-${index}`}><span>{item.label}</span><small>{item.from} → {item.to}</small></div>)}</div>}<div className="admin-locked-target"><span>目标好友</span><b>{task.name}</b><small>稳定身份已锁定，编辑不会重新选择好友</small></div>{status&&<div className="inline-feedback">{status}</div>}<div className="setting-actions"><Button type="submit" busy={saving} disabled={!dirty}>保存任务</Button><Button variant="quiet" type="button" onClick={closeEditor}>取消</Button></div></form></div></Drawer>;
}
function AdminUserWorkspace({workspace, plans=[], csrf, actorId, onClose, onRefresh, onTaskEdit}) {
  const user=workspace?.user||{}; const membership=workspace?.membership||{};
  const accounts=asList(workspace?.accounts), tasks=asList(workspace?.tasks), runs=asList(workspace?.runs);
  const [incidentAccount,setIncidentAccount]=useState(null);
  const [limits,setLimits]=useState({accounts:1,tasks:1});
  const [note,setNote]=useState('');
  const [planId,setPlanId]=useState(plans[0]?.id||'');
  const [activeTab,setActiveTab]=useState(workspace?.initialTab||'overview');
  const [feedback,setFeedback]=useState({});
  const [password,setPassword]=useState('');
  const [busy,setBusy]=useState('');
  const operationLock=useRef(false);
  const finiteTaskLimit=useRef(10);
  const contentRef=useRef(null);
  const passwordRef=useRef(null);
  useViewportReveal(contentRef,!!workspace,{scrollRoot:true});
  useEffect(()=>{if(password)passwordRef.current?.scrollIntoView({block:'nearest',behavior:'instant'})},[password]);
  useEffect(()=>{
    setLimits({accounts:membership.max_accounts||1,tasks:membership.max_tasks||1});
    if(membership.max_tasks<UNLIMITED_TASKS)finiteTaskLimit.current=membership.max_tasks||10;
  },[user.id,membership.max_accounts,membership.max_tasks]);
  useEffect(()=>setNote(user.admin_note||''),[user.id,user.admin_note]);
  useEffect(()=>{setActiveTab(workspace?.initialTab||'overview');setFeedback({});setPassword('');setIncidentAccount(null)},[user.id,workspace?.initialTab]);
  useEffect(()=>{if(plans.length&&!plans.some(plan=>plan.id===planId))setPlanId(plans[0].id)},[plans,planId]);
  if(!workspace)return null;
  const overLimit=membershipOverLimit(membership);
  const successfulRuns=runs.filter(run=>run.status==='success').length;
  const runSuccessRate=runs.length?Math.round(successfulRuns/runs.length*100):0;
  const selectedPlan=plans.find(plan=>plan.id===planId)||plans[0]||null;
  const taskQuota=value=>Number(value)>=UNLIMITED_TASKS?'不限':String(value);
  const limitDiff=[['账号额度',membership.max_accounts||1,Number(limits.accounts)],['任务额度',membership.max_tasks||1,Number(limits.tasks)]].filter(([,before,after])=>before!==after);
  const planDiff=selectedPlan?[['账号额度',membership.max_accounts||1,selectedPlan.max_accounts||1],['任务额度',membership.max_tasks||1,planMaxTasks(selectedPlan)]].filter(([,before,after])=>before!==after):[];
  const noteDirty=note!==(user.admin_note||'');
  const canClose=()=>!operationLock.current&&(!(noteDirty||limitDiff.length)||confirmModal({title:'放弃未保存的修改？',message:'用户备注或额度尚未保存。',confirmLabel:'放弃修改',cancelLabel:'继续编辑'}));
  const report=(section,message,error=false)=>setFeedback(value=>({...value,[section]:{message,error}}));
  const resultFor=section=>feedback[section]?.message&&<div key={feedback[section].message} className={`workspace-feedback${feedback[section].error?' is-error':''}`} role={feedback[section].error?'alert':'status'}><StatusIcon kind={feedback[section].error?'error':'check'}/><span>{feedback[section].message}</span></div>;
  const json=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,...body})});
  const mutate=async(section,path,options,success)=>{
    if(operationLock.current)return;
    operationLock.current=true;setBusy(section);report(section,'');
    try {
      const value=await api(path,options);
      report(section,typeof success==='function'?success(value):success||value?.message||'操作已完成');
      try {await onRefresh()}catch {report(section,'操作已完成，但数据刷新失败，请关闭后重新打开查看。',true)}
      return value;
    }catch(error){report(section,error.message,true)}finally{operationLock.current=false;setBusy('')}
  };
  const reset=async()=>{
    const value=await mutate('security',`/api/admin/users/${user.id}/reset-password`,json({}),'密码已重置，请将临时密码告知用户。');
    if(value?.temporary_password)setPassword(value.temporary_password);
  };
  const toggle=()=>mutate('security',`/api/admin/users/${user.id}/toggle`,json({}),user.status==='active'?'用户已停用':'用户已启用');
  const changeRole=async()=>{
    const nextRole=user.role==='admin'?'user':'admin'; const label=nextRole==='admin'?'升级为管理员':'降为普通用户';
    if(await confirmModal({title:label,message:`确定将 ${user.username} ${label}吗？`}))return mutate('security',`/api/admin/users/${user.id}/role`,json({role:nextRole}),'用户角色已更新');
  };
  const extend=async()=>{
    if(!selectedPlan)return;
    const changes=planDiff.length?planDiff.map(([name,before,after])=>`${name}：${taskQuota(before)} → ${taskQuota(after)}`).join('\n'):'账号和任务额度不变';
    if(!await confirmModal({title:'确认续期套餐',message:`套餐：${selectedPlan.name}\n增加时长：${planDurationLabel(selectedPlan)}\n${changes}\n续期后有效期将顺延；原有任务和权益保留。`,confirmLabel:'确认续期'}))return;
    return mutate('subscription',`/admin/users/${user.id}/subscription`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrf_token:csrf,duration_days:String(planDurationDays(selectedPlan)),plan_id:selectedPlan.id,max_accounts:String(selectedPlan.max_accounts||1),max_tasks:String(planMaxTasks(selectedPlan))})},'订阅已续期');
  };
  const saveLimits=async e=>{
    e.preventDefault();if(!limitDiff.length)return;
    if(!await confirmModal({title:'确认调整客户额度',message:limitDiff.map(([name,before,after])=>`${name}：${taskQuota(before)} → ${taskQuota(after)}`).join('\n'),confirmLabel:'确认调整'}))return;
    return mutate('limits',`/api/admin/subscriptions/${user.id}/limits`,json({max_accounts:Number(limits.accounts),max_tasks:Number(limits.tasks)}),value=>`额度已更新：${value.max_accounts} 个账号 / ${taskQuota(value.max_tasks)} 个任务`);
  };
  const saveNote=async e=>{e.preventDefault();return mutate('note',`/api/admin/users/${user.id}/note`,json({admin_note:note}),'用户备注已保存')};
  const revoke=async()=>{
    if(await confirmModal({title:'撤销订阅',message:`确定撤销 ${user.username} 的订阅吗？撤销后保留 1 个免费好友任务执行，其他任务暂不执行。`}))return mutate('security',`/api/admin/subscriptions/${user.id}/revoke`,json({}),'订阅已撤销');
  };
  const taskAction=task=>mutate('overview',`/api/admin/tasks/${task.id}/enabled`,json({enabled:!task.enabled}),task.enabled?'任务已暂停':'任务已恢复');
  const diffPreview=(title,changes)=><div className="workspace-diff"><b>{title}</b>{changes.map(([label,before,after])=><div key={label}><span>{label}</span><span><s>{taskQuota(before)}</s><span aria-hidden="true"> → </span><strong>{taskQuota(after)}</strong></span></div>)}</div>;
  return <Drawer open onClose={onClose} canClose={canClose} title={user.username||'用户管理'} wide size="customer" footer={close=><footer className="customer-workspace-footer"><span>{noteDirty||limitDiff.length?'有未保存修改':''}</span><Button variant="quiet" disabled={!!busy} onClick={close}>完成</Button></footer>}>
    {incidentAccount&&<React.Suspense fallback={<LoadingState/>}><AccountIncidents account={incidentAccount} onClose={()=>setIncidentAccount(null)} api={api} Drawer={Drawer} Badge={Badge} LoadingState={LoadingState}/></React.Suspense>}
    <div className="customer-workspace-head">
      <div className="customer-identity"><Badge tone={user.status==='active'?'good':'danger'}>{user.status==='active'?'正常':'已停用'}</Badge><span>{user.role==='admin'?'管理员':'普通用户'}</span><span>注册于 {user.created||'—'}</span></div>
      {activeTab==='overview'&&user.admin_note&&<p className="customer-identity-note"><span>备注</span>{user.admin_note}</p>}
      <div className="workspace-tabs" data-active={activeTab} role="group" aria-label="用户工作区">
        <button type="button" aria-pressed={activeTab==='overview'} className={activeTab==='overview'?'active':''} onClick={()=>setActiveTab('overview')}>概览</button>
        <button type="button" aria-pressed={activeTab==='manage'} className={activeTab==='manage'?'active':''} onClick={()=>setActiveTab('manage')}>管理</button>
      </div>
    </div>
    <div ref={contentRef} className="drawer-content customer-workspace-content">
      <AnimatePresence mode="wait" initial={false} onExitComplete={()=>{if(contentRef.current)contentRef.current.scrollTop=0}}>
      {activeTab==='overview'?<TabTransitionPanel key="overview">
        <div className="customer-summary-stats" aria-label="用户使用情况">
          <div><span>抖音账号</span><strong>{workspace.resource_limits?.accounts_total??accounts.length}</strong><small>已绑定</small></div>
          <div><span>续火任务</span><strong>{workspace.resource_limits?.tasks_total??tasks.length}</strong><small>{overLimit>0?`超出 ${overLimit} 个`:'全部任务'}</small></div>
          <div><span>最近执行</span><strong>{runs.length}</strong><small>已加载记录</small></div>
          <div><span>近期成功率</span><strong>{runs.length?`${runSuccessRate}%`:'—'}</strong><small>{successfulRuns} / {runs.length} 次</small></div>
        </div>
        {resultFor('overview')}
        <section data-reveal="section" className="customer-section">
          <div className="customer-section-heading"><h3>账号与状态</h3><span>{accounts.length} 个账号</span></div>
          {accounts.length?accounts.map(account=><div className="customer-list-row customer-account" key={account.id}>
            <div><b>{account.name}</b><small>{account.profile?.nickname||'抖音账号'}{account.incident?` · 影响 ${account.incident.affected_tasks} 个任务`:''}</small></div>
            <Badge tone={account.validation_state==='valid'?'good':'warn'}>{account.health?.label||(account.validation_state==='valid'?'正常':'待复核')}</Badge><Button variant="quiet" disabled={!!busy} onClick={()=>setIncidentAccount(account)}>异常记录</Button>
          </div>):<Empty title="未绑定抖音账号"/>}
        </section>
        <section data-reveal="section" className="customer-section">
          <div className="customer-section-heading"><h3>任务与好友</h3><span>{tasks.length} 个任务</span></div>
          {tasks.length?tasks.map(task=><div className="customer-list-row customer-task" key={task.id}>
            <div><b>{task.name}</b><small>{task.time} · {task.kind}</small></div><Badge tone={task.enabled?'good':'neutral'}>{task.enabled?'已启用':'已暂停'}</Badge>
            <div className="customer-row-actions"><Button variant="quiet" disabled={!!busy} onClick={()=>onTaskEdit?.(task)}>编辑</Button><Button variant="quiet" disabled={!!busy} onClick={()=>taskAction(task)}>{task.enabled?'暂停':'恢复'}</Button></div>
          </div>):<Empty title="暂无续火任务"/>}
        </section>
        <section data-reveal="section" className="customer-section">
          <div className="customer-section-heading"><h3>最近执行</h3><span>最近 {Math.min(runs.length,6)} 条</span></div>
          {runs.length?runs.slice(0,6).map(run=><div className="customer-list-row customer-run" key={run.id}><div><b>{run.name}</b><small>{run.time}{run.error?` · ${run.error}`:''}</small></div><Badge tone={run.status==='success'?'good':run.status==='failed'?'danger':'warn'}>{run.label||run.status}</Badge></div>):<Empty title="暂无执行记录"/>}
        </section>
      </TabTransitionPanel>:<TabTransitionPanel key="manage">
        <form className="customer-note-form" onSubmit={saveNote}>
          <label htmlFor="customer-note">备注</label><input id="customer-note" className="input" aria-label="管理员备注" maxLength="500" disabled={!!busy} value={note} onChange={e=>{setNote(e.target.value);report('note','')}} placeholder="姓名或昵称"/>
          <Button type="submit" variant="quiet" busy={busy==='note'} disabled={!!busy||!noteDirty}>保存备注</Button>{resultFor('note')}
        </form>
        <section data-reveal="section" className="customer-section customer-membership">
          <div className="customer-section-heading"><h3>订阅与额度</h3><Badge tone={membership.active?'good':'neutral'}>{membership.status_label||(membership.active?'有效':'未开通')}</Badge></div>
          <div className="customer-plan-summary"><div><small>有效期至</small><b>{membershipDate(membership.valid_until)}</b></div>{membership.active&&<div><small>剩余时间</small><b>{membership.days_remaining==null?'长期有效':`${membership.days_remaining} 天`}</b></div>}</div>
          <div className="customer-renew-row"><label>续期套餐<select className="input workspace-plan-select" aria-label="续期套餐" value={planId} onChange={e=>setPlanId(e.target.value)} disabled={!!busy||!plans.length}>{plans.map(plan=><option value={plan.id} key={plan.id}>{plan.name} · ¥{plan.price_yuan} / {planDurationLabel(plan)}</option>)}</select></label><Button disabled={!!busy||!selectedPlan} onClick={extend}>续期 {selectedPlan?planDurationLabel(selectedPlan):'套餐'}</Button></div>
          {selectedPlan&&<p className="customer-hint">{selectedPlan.max_accounts||1} 个账号 · {planTaskLabel(selectedPlan)} · 有效期顺延 {planDurationLabel(selectedPlan)}</p>}
          {!!planDiff.length&&diffPreview('续期后额度变化',planDiff)}
          <MembershipPlanNotice membership={membership}/>{resultFor('subscription')}
          {user.role!=='admin'&&<form onSubmit={saveLimits} className="customer-quota-form"><div className="customer-quota-heading"><h4>使用额度</h4><span>调整后到期时间不变</span></div><div className="customer-quota-fields">
            <label>账号额度<input className="input" type="number" min="1" max="100" required disabled={!!busy} value={limits.accounts} onChange={e=>setLimits(v=>({...v,accounts:e.target.value}))}/></label>
            <label>任务额度<input className="input" type={Number(limits.tasks)>=UNLIMITED_TASKS?'text':'number'} min="1" max={UNLIMITED_TASKS-1} required disabled={!!busy||Number(limits.tasks)>=UNLIMITED_TASKS} value={Number(limits.tasks)>=UNLIMITED_TASKS?'不限任务':limits.tasks} onChange={e=>{finiteTaskLimit.current=e.target.value;setLimits(v=>({...v,tasks:e.target.value}))}}/></label>
          </div>{!!limitDiff.length&&diffPreview('待保存的额度',limitDiff)}{resultFor('limits')}<div className="customer-form-actions"><label className="customer-check"><input type="checkbox" checked={Number(limits.tasks)>=UNLIMITED_TASKS} disabled={!!busy} onChange={e=>setLimits(v=>({...v,tasks:e.target.checked?UNLIMITED_TASKS:finiteTaskLimit.current||10}))}/>不限任务</label><Button type="submit" variant="quiet" busy={busy==='limits'} disabled={!!busy||!limitDiff.length}>保存额度</Button></div></form>}
        </section>
        <section data-reveal="section" className="customer-section customer-security">
          <div className="customer-section-heading"><div><h3>账号安全</h3></div></div>
          <div className="customer-security-row"><div><b>网站密码</b><p>重置后退出该用户的登录会话。</p></div><Button variant="quiet" disabled={!!busy} onClick={reset}>重置密码</Button></div>
          {password&&<div ref={passwordRef} className="customer-password"><span>临时密码</span><code>{password}</code><Button variant="quiet" onClick={async()=>{try{await navigator.clipboard.writeText(password);report('security','临时密码已复制')}catch{report('security','复制失败，请手动选择上方临时密码',true)}}}>复制密码</Button></div>}
          {user.id!==actorId&&<div className="customer-security-row"><div><b>用户角色</b><p>当前为{user.role==='admin'?'管理员':'普通用户'}。</p></div><Button variant="quiet" disabled={!!busy} onClick={changeRole}>{user.role==='admin'?'降为普通用户':'升级为管理员'}</Button></div>}
          <div className="customer-security-row"><div><b>账号状态</b><p>{user.status==='active'?'停用后，用户将无法登录网站。':'恢复该用户的网站访问。'}</p></div><Button variant={user.status==='active'?'danger':'quiet'} disabled={!!busy||user.id===actorId} onClick={toggle}>{user.status==='active'?'停用用户':'启用用户'}</Button></div>
          {user.role!=='admin'&&<div className="customer-security-row"><div><b>撤销订阅</b><p>保留 1 个免费好友任务，其余任务暂不执行。</p></div><Button variant="danger" disabled={!!busy||!membership.active} onClick={revoke}>撤销订阅</Button></div>}
          {resultFor('security')}
        </section>
      </TabTransitionPanel>}
      </AnimatePresence>
    </div>
  </Drawer>;
}
function CapacitySummary({business}) {
  if(!business)return null;
  const capacity=business.capacity;
  return <Card><div className="section-head"><div><h2>任务负载与安全余量</h2></div></div><p className="muted-copy">近 24 小时已记录计划的成功率 {business.recorded_plan_success_percent==null?'暂无样本':`${business.recorded_plan_success_percent}%`} · 跳过 {business.skipped_runs||0} 条 · 排队 P95 {business.queue_delay_p95_seconds??'—'} 秒</p><div className="form-grid">{capacity?.peaks?.map(peak=><div key={peak.window_minutes}><b>{peak.start} 起 {peak.window_minutes} 分钟</b><p>{peak.tasks} 条任务 · {peak.estimated_drain_seconds==null?'执行样本不足':`预计消化 ${Math.ceil(peak.estimated_drain_seconds/60)} 分钟`}</p></div>)}</div><p className="muted-copy">等待登录/复核 {business.queued_logins??0} 个 · 等待同步 {business.queued_directory_jobs??0} 个 · 持续异常 {business.active_incidents??0} 个 · 已恢复事件 P95 {business.recovery_p95_seconds==null?'暂无样本':`${business.recovery_p95_seconds} 秒`}</p><small className="muted">按单槽执行 P95 与保守发送间隔估算，不假定所有账号可并行；不包含平台验证等待，不能作为实际发送承诺。</small></Card>;
}

return Admin;
}
