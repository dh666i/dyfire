import React, {useEffect, useRef, useState} from 'react';
import {cloudFrameOverlay,createCloudFrameState,frameLoadError,frameLoaded,retryCloudFrame} from './cloud-frame-state.js';

export function createBindingComponents({api,Button,interactionRequestId,sendCloudAction}) {
function ScanVerification({scan,data,setScan}) {
  const [value,setValue]=useState(''); const [sending,setSending]=useState(false); const [notice,setNotice]=useState('');
  const needs=Boolean(scan?.needs_verification);
  if(!needs||!['sms','password'].includes(scan?.verification_kind))return null;
  const password=scan?.verification_kind==='password';
  const submit=async e=>{e.preventDefault();if(!value.trim()||!scan?.id)return;setSending(true);setNotice('正在提交验证…');try{const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','text');fd.append('request_id',interactionRequestId());fd.append('text',value);const accepted=await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd});setValue('');let state=accepted;for(let attempt=0;attempt<24&&['queued','processing'].includes(state?.status);attempt++){await new Promise(resolve=>setTimeout(resolve,650));state=await api(state.status_url)}setNotice(state?.status==='delivered'?'云端已确认验证输入':state?.status==='failed'?'云端未接受验证输入，请查看画面后重试':state?.status==='uncertain'?'验证结果暂时无法确认，请查看云端页面':'已提交，等待抖音确认');const latest=await api(`/accounts/scan/${scan.id}`);if(latest&&typeof latest==='object')setScan(latest)}catch(err){setNotice(err.message)}finally{setSending(false)}};
  return <form className="verification-panel" onSubmit={submit}><div className="verification-title"><span className="verification-icon">{password?'⌁':'✉'}</span><div><b>{password?'输入抖音登录密码':'短信验证码验证'}</b><small>{password?'请在此输入抖音账号密码，提交一次即可。':scan?.masked_phone?`验证码将发送至 ${scan.masked_phone}`:'请填写抖音页面要求的验证码。'}</small></div></div><label className="verification-field"><span>{password?'登录密码':'验证码'}</span><input className="input" type={password?'password':'text'} inputMode={password?'text':'numeric'} autoComplete={password?'current-password':'one-time-code'} value={value} onChange={e=>setValue(e.target.value)} placeholder={password?'输入密码':'输入验证码'}/></label><div className="verification-actions"><Button type="submit" disabled={sending||!value.trim()}>{sending?'提交中…':'提交验证'}</Button>{!password&&<span className="verification-hint">{scan?.resend_after_seconds?`${scan.resend_after_seconds}s 后可重试`:scan?.verification_sent?'验证码已发送':'等待抖音发送验证码'}</span>}</div>{notice&&<div className="inline-feedback">{notice}</div>}</form>;
}

function CloudInteraction({scan,data}) {
  const [value,setValue]=useState(''); const [busy,setBusy]=useState(false); const [status,setStatus]=useState('');
  if ((scan?.needs_verification&&!['app','other'].includes(scan?.verification_kind)) || (scan?.mode==='recovery'&&!scan?.needs_verification)) return null;
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
  const [frame,setFrame]=useState(()=>createCloudFrameState(scan?.id)); const [pointer,setPointer]=useState({x:50,y:50}); const [keyboardActive,setKeyboardActive]=useState(false); const [remoteStatus,setRemoteStatus]=useState(''); const dragPoints=useRef([]); const suppressClick=useRef(false); const preview=scan?.id==='preview-scan';
  const state=frame.status; const attempt=frame.attempt; const frameError=frame.error;
  useEffect(()=>{setFrame(createCloudFrameState(scan?.id));setPointer({x:50,y:50});setKeyboardActive(false)},[scan?.id]);
  const sendClick=async (x,y)=>{if(!scan?.id||state!=='ready'||frameError)return;const fd=new FormData();fd.append('csrf_token',data?.csrf||'');fd.append('kind','click');fd.append('x',String(Math.max(0,Math.min(1,x/100))));fd.append('y',String(Math.max(0,Math.min(1,y/100))));try{await api(`/accounts/scan/${scan.id}/interact`,{method:'POST',body:fd})}catch(err){onError?.(err.message)}};
  const point=e=>{const rect=e.currentTarget.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(e.clientY-rect.top)/rect.height))}};
  const click=async e=>{if(suppressClick.current){suppressClick.current=false;return}const p=point(e);setPointer({x:p.x*100,y:p.y*100});await sendClick(p.x*100,p.y*100)};
  const pointerDown=e=>{if(e.button!==0)return;e.currentTarget.setPointerCapture?.(e.pointerId);const p=point(e);dragPoints.current=[p]};
  const pointerMove=e=>{if(!dragPoints.current.length)return;const p=point(e),last=dragPoints.current.at(-1);if(Math.hypot(p.x-last.x,p.y-last.y)>.006&&dragPoints.current.length<40)dragPoints.current.push(p);setPointer({x:p.x*100,y:p.y*100})};
  const pointerUp=async e=>{if(!dragPoints.current.length)return;const points=dragPoints.current;dragPoints.current=[];if(points.length<3)return;suppressClick.current=true;setRemoteStatus('正在执行云端拖动…');try{const value=await sendCloudAction(scan,data,'drag',{points});setRemoteStatus(value.status==='delivered'?'云端拖动已完成':value.status==='uncertain'?'拖动结果未确认，请查看画面':value.status==='failed'?'云端未执行拖动':'等待云端结果')}catch(error){setRemoteStatus(error.message)}};
  const remoteKey=async key=>{setRemoteStatus(`正在发送 ${key}…`);try{const value=await sendCloudAction(scan,data,'key',{key});setRemoteStatus(value.status==='delivered'?`${key} 已发送到云端`:value.status==='uncertain'?'按键结果未确认':value.status==='failed'?'云端未接受按键':'等待云端结果')}catch(error){setRemoteStatus(error.message)}};
  const remoteScroll=async delta_y=>{setRemoteStatus('正在滚动云端页面…');try{const value=await sendCloudAction(scan,data,'scroll',{x:pointer.x/100,y:pointer.y/100,delta_y});setRemoteStatus(value.status==='delivered'?'云端页面已滚动':value.status==='uncertain'?'滚动结果未确认':value.status==='failed'?'云端未执行滚动':'等待云端结果')}catch(error){setRemoteStatus(error.message)}};
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
  return <><div className={`scan-stage ${state==='ready'?'frame-ready':''} ${frameClass}`}><img className="scan-frame-image" onLoad={()=>{setFrame(current=>frameLoaded(current));onReady?.();document.body.dataset.dyfireFrameReady='true';document.dispatchEvent(new CustomEvent('dyfire-frame-ready'))}} onError={()=>setFrame(current=>frameLoadError(current,{remainingSeconds:scan?.remaining_seconds}))} onClick={click} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={()=>{dragPoints.current=[]}} onKeyDown={keyDown} onFocus={()=>setKeyboardActive(true)} onBlur={()=>setKeyboardActive(false)} role="button" tabIndex="0" aria-label="抖音云端登录页面。点击操作，拖动可完成页面要求的滑动验证，方向键移动点击位置" aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Enter Space" src={`/accounts/scan/${scan.id}/qr?attempt=${attempt}${revision?`&revision=${encodeURIComponent(revision)}`:''}`} alt="抖音云端登录页面"/>{keyboardActive&&<span className="scan-keyboard-pointer" aria-hidden="true" style={{left:`${pointer.x}%`,top:`${pointer.y}%`}}/>}<div className="scan-loading-overlay" aria-live="polite">{overlay.kind==='error'?<><b>{overlay.title}</b><small>{overlay.detail}</small>{overlay.title!=='二维码已失效'&&<Button type="button" variant="quiet" onClick={retry}>重试加载画面</Button>}</>:<><span className="scan-loading-spinner"/><b>{overlay.title}</b><small>{overlay.detail|| (sessionActive?'云端容器启动后会自动显示，请稍候。':'正在确认登录会话状态，请稍候。')}</small></>}</div></div>{state==='ready'&&!frameError&&<div className="cloud-frame-controls" aria-label="云端页面控制"><span>拖动云端画面可操作滑块</span><Button type="button" variant="quiet" onClick={()=>remoteScroll(-520)}>向上滚动</Button><Button type="button" variant="quiet" onClick={()=>remoteScroll(520)}>向下滚动</Button><Button type="button" variant="quiet" onClick={()=>remoteKey('Tab')}>Tab</Button><Button type="button" variant="quiet" onClick={()=>remoteKey('Enter')}>Enter</Button><Button type="button" variant="quiet" onClick={()=>remoteKey('Escape')}>Esc</Button>{remoteStatus&&<small role="status">{remoteStatus}</small>}</div>}</>;
}

function BindingProgress({scan}) { const status=String(scan?.status||'').toLowerCase(); const recovery=scan?.mode==='recovery'; const current=scan?.status==='succeeded'?4:scan?.needs_verification?3:['queued','starting','initializing','pending','created'].includes(status)?1:2; const labels=recovery?['准备原账号','检查登录状态','完成复核','账号恢复']:['准备环境','扫码确认','验证输入','绑定完成']; return <div className="scan-progress scan-progress-four" aria-label={`${recovery?'账号复核':'绑定'}进度：${labels[current-1]}`}>{labels.map((label,index)=><React.Fragment key={label}><span className={`scan-progress-step ${index+1<current?'complete':''} ${index+1===current?'active':''}`}><i className="scan-progress-dot"/><span>{label}</span></span>{index<labels.length-1&&<span className={`scan-progress-line ${index+1<current?'complete':''}`} aria-hidden="true"/>}</React.Fragment>)}</div>; }


return {ScanVerification,CloudInteraction,CloudFrame,BindingProgress};
}
