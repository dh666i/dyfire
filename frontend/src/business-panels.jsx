import React, {useEffect, useRef, useState} from 'react';

export function createBusinessPanels({api,Card,Badge,Button}) {
function AnnouncementInbox({data}) {
  const [items,setItems]=useState([]);const [error,setError]=useState('');
  useEffect(()=>{let active=true;api('/api/announcements').then(value=>{if(active)setItems(value.items||[])}).catch(value=>{if(active)setError(value.message)});return()=>{active=false}},[]);
  const markRead=async item=>{try{await api(`/api/announcements/${item.id}/read`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data?.csrf||''})});setItems(rows=>rows.map(row=>row.id===item.id?{...row,read:true}:row))}catch(value){setError(value.message)}};
  const unread=items.filter(item=>!item.read);
  if(!unread.length&&!error)return null;
  return <Card className="announcement-inbox"><div className="section-head"><div><h2>服务公告</h2></div><Badge tone={unread.length?'warn':'neutral'}>{unread.length} 条未读</Badge></div>{error&&<p className="inline-feedback danger-text">{error}</p>}{unread.map(item=><article className="announcement-row" key={item.id}><div><b>{item.title}</b><p>{item.body}</p><small>有效至 {new Date(item.expires_at).toLocaleString('zh-CN')}</small></div><Button variant="quiet" onClick={()=>markRead(item)}>标为已读</Button></article>)}</Card>;
}

function AdminBusinessPanel({data,onFeedback}) {
  const [customerQuery,setCustomerQuery]=useState('');const [customers,setCustomers]=useState([]);const [customerPage,setCustomerPage]=useState(1);const [customerPagination,setCustomerPagination]=useState(null);const [announcements,setAnnouncements]=useState([]);const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState({title:'',body:'',user_id:'',days:7});
  const knownCustomers=useRef(new Map());
  const customerChoices=id=>id&&!customers.some(user=>user.id===id)?[{id,username:knownCustomers.current.get(id)||id},...customers]:customers;

  useEffect(()=>{
    let active=true;
    api('/api/admin/announcements').then(value=>{if(active)setAnnouncements(value.items||[])}).catch(error=>{if(active)onFeedback(error.message)});
    return()=>{active=false};
  },[]);
  useEffect(()=>{
    const controller=new AbortController();
    const timer=setTimeout(()=>api(`/api/admin/customer-options?q=${encodeURIComponent(customerQuery)}&page=${customerPage}`,{signal:controller.signal}).then(value=>{
      if(controller.signal.aborted)return;
      setCustomers(value.items||[]);(value.items||[]).forEach(user=>knownCustomers.current.set(user.id,user.username));setCustomerPagination(value.pagination);
    }).catch(error=>{if(error.name!=='AbortError')onFeedback(error.message)}),250);
    return()=>{clearTimeout(timer);controller.abort()};
  },[customerQuery,customerPage]);

  const publish=async event=>{
    event.preventDefault();setBusy(true);
    try{
      await api('/api/admin/announcements',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf,...notice})});
      const latest=await api('/api/admin/announcements');
      setNotice({title:'',body:'',user_id:'',days:7});setAnnouncements(latest.items||[]);onFeedback('公告已发布');
    }catch(error){onFeedback(error.message)}finally{setBusy(false)}
  };
  const withdraw=async item=>{
    try{await api(`/api/admin/announcements/${item.id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:data.csrf})});setAnnouncements(rows=>rows.map(row=>row.id===item.id?{...row,withdrawn:true}:row))}
    catch(error){onFeedback(error.message)}
  };

  return <>
    <Card className="admin-customer-lookup"><label>查找客户<input className="input" placeholder="按用户名查找公告接收对象" value={customerQuery} onChange={e=>{setCustomerQuery(e.target.value);setCustomerPage(1)}}/></label><div className="form-actions"><Button variant="quiet" disabled={!customerPagination?.has_previous} onClick={()=>setCustomerPage(value=>value-1)}>上一组客户</Button><span>第 {customerPage} 页 · {customerPagination?.total??'—'} 位客户</span><Button variant="quiet" disabled={!customerPagination?.has_next} onClick={()=>setCustomerPage(value=>value+1)}>下一组客户</Button></div></Card>
    <Card className="admin-announcements"><div className="section-head"><div><h2>发布公告</h2></div></div><form className="form-stack" onSubmit={publish}><label>标题<input className="input" maxLength="160" required value={notice.title} onChange={e=>setNotice(value=>({...value,title:e.target.value}))}/></label><label>内容<textarea className="input textarea" maxLength="5000" required value={notice.body} onChange={e=>setNotice(value=>({...value,body:e.target.value}))}/></label><div className="form-grid"><label>接收对象<select className="input" value={notice.user_id} onChange={e=>setNotice(value=>({...value,user_id:e.target.value}))}><option value="">全体用户</option>{customerChoices(notice.user_id).map(user=><option key={user.id} value={user.id}>{user.username}</option>)}</select></label><label>有效天数<input className="input" type="number" min="1" max="365" value={notice.days} onChange={e=>setNotice(value=>({...value,days:e.target.value}))}/></label></div><Button type="submit" disabled={busy}>发布公告</Button></form><div className="admin-announcement-list">{announcements.map(item=><div className="announcement-row" key={item.id}><div><b>{item.title}</b><p>{item.body}</p><small>{item.user_id?'定向通知':'全体通知'} · {item.withdrawn?'已撤回':`有效至 ${new Date(item.expires_at).toLocaleDateString('zh-CN')}`}</small></div>{!item.withdrawn&&<Button variant="quiet" onClick={()=>withdraw(item)}>撤回</Button>}</div>)}</div></Card>
  </>;
}

return {AnnouncementInbox,AdminBusinessPanel};
}
