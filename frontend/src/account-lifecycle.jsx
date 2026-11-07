import React,{useState} from 'react';

export default function AccountLifecycle({account,csrf,api,confirmModal,onRefresh}) {
  const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
  const perform=async unlink=>{
    if(busy)return;
    if(!await confirmModal({title:unlink?'解除抖音账号绑定':'停止该账号托管',message:unlink?`将解除 ${account.name} 的绑定，清除云端登录凭据和已同步好友缓存。该账号的任务会暂停并保留历史执行记录。`:`将暂停 ${account.name} 的全部续火任务。账号登录资料保留，恢复时可在任务页启用。`,confirmLabel:unlink?'确认解绑':'停止托管'}))return;
    setBusy(true);setNotice('');
    try{
      const value=await api(unlink?`/api/accounts/${account.id}`:`/api/accounts/${account.id}/stop-hosting`,{method:unlink?'DELETE':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf})});
      setNotice(value.message);await onRefresh?.();
    }catch(error){setNotice(error.message)}finally{setBusy(false)}
  };
  return <div className="account-lifecycle"><details><summary>管理托管</summary><div><button type="button" disabled={busy} onClick={()=>perform(false)}>停止托管</button><button type="button" className="danger-text" disabled={busy} onClick={()=>perform(true)}>解除绑定</button></div></details>{notice&&<small role="status">{notice}</small>}</div>;
}
