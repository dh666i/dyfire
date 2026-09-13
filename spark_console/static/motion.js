(() => {
  'use strict';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const closing=new WeakMap();
  window.sparkCancelClose=dialog=>{const entry=closing.get(dialog);if(entry){closing.delete(dialog);clearTimeout(entry.timer);entry.animation.cancel();}};
  window.sparkCloseDrawer=(dialog,complete)=>{
    if(closing.has(dialog))return;
    if(!dialog.open||reduced.matches||!dialog.animate){complete();return;}
    const animation=dialog.animate([{opacity:1,transform:'translateX(0)'},{opacity:0,transform:'translateX(18px)'}],{duration:160,easing:'ease-in',fill:'forwards'});
    const finish=()=>{if(closing.get(dialog)!==entry)return;closing.delete(dialog);clearTimeout(entry.timer);complete();animation.cancel();};
    const entry={animation,timer:setTimeout(finish,220)};closing.set(dialog,entry);animation.onfinish=finish;
  };
  window.sparkEmphasize=row=>{if(!row)return;row.classList.remove('task-operation-updated');void row.offsetWidth;row.classList.add('task-operation-updated');};
  const folds=new WeakMap();
  window.sparkFoldRows=(rows,expanded,complete)=>{
    if(reduced.matches||!rows.length||!rows[0].animate){complete();return;}
    const animations=rows.map(row=>{const style=getComputedStyle(row),full={height:row.getBoundingClientRect().height+'px',minHeight:'0px',paddingTop:style.paddingTop,paddingBottom:style.paddingBottom,opacity:1,overflow:'hidden'},empty={height:'0px',minHeight:'0px',paddingTop:'0px',paddingBottom:'0px',opacity:0,overflow:'hidden'};return row.animate(expanded?[empty,full]:[full,empty],{duration:180,easing:'ease-out',fill:'forwards'});});
    let done=false;const finish=()=>{if(done)return;done=true;clearTimeout(timer);complete();animations.forEach(a=>a.cancel());};
    const timer=setTimeout(finish,240);animations[0].onfinish=finish;
  };
  window.sparkFold=(body,expanded)=>{
    const start=body.hidden?0:body.getBoundingClientRect().height;
    const previous=folds.get(body);if(previous){previous.cancel();folds.delete(body);}
    if(reduced.matches||!body.animate){body.hidden=!expanded;return;}
    body.hidden=false;
    const end=expanded?body.scrollHeight:0;
    const animation=body.animate([{height:start+'px',overflow:'hidden'},{height:end+'px',overflow:'hidden'}],{duration:180,easing:'cubic-bezier(.2,.7,.3,1)'});
    folds.set(body,animation);animation.onfinish=()=>{if(folds.get(body)!==animation)return;body.hidden=!expanded;folds.delete(body);animation.cancel();};
  };
  function tabs(){
    document.querySelectorAll('.filters,.studio-segments').forEach(group=>{
      if(group.dataset.motionTabs)return;group.dataset.motionTabs='true';
      const line=document.createElement('i');line.className='motion-tab-line';line.setAttribute('aria-hidden','true');group.append(line);
      const update=()=>{const active=group.querySelector('button[aria-pressed=true],button.selected');if(!active)return;line.style.width=active.offsetWidth+'px';line.style.transform=`translateX(${active.offsetLeft}px)`;};
      new MutationObserver(update).observe(group,{subtree:true,attributes:true,attributeFilter:['aria-pressed','class']});
      if(window.ResizeObserver)new ResizeObserver(update).observe(group);update();document.fonts?.ready.then(update);
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',tabs,{once:true});else tabs();
  const viewport=window.visualViewport;
  const resize=()=>document.documentElement.style.setProperty('--visible-height',(viewport?.height||innerHeight)+'px');
  viewport?.addEventListener('resize',resize);resize();
  window.sparkReveal = node => {
    if (!node || reduced.matches || !node.animate) return;
    node.getAnimations().forEach(animation => animation.cancel());
    node.animate([{opacity: .65}, {opacity: 1}], {duration: 160});
  };
  const images = new WeakSet();
  function avatars(root) {
    root.querySelectorAll('[data-avatar-image], .target-option-avatar img').forEach(img => {
      if (images.has(img)) return;
      images.add(img);
      const ready = () => {img.classList.remove('motion-avatar-pending');img.classList.add('motion-avatar-ready');};
      if (img.complete) {if (img.naturalWidth) ready();else img.remove();return;}
      img.classList.add('motion-avatar-pending');
      img.addEventListener('load', ready, {once:true});
      img.addEventListener('error', () => img.remove(), {once:true});
    });
  }
  avatars(document);
  const main = document.querySelector('main');
  if (main) new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType === 1) avatars(node.parentElement || node);
    }
  }).observe(main, {childList:true,subtree:true});
  // Represent only observed login states, never an invented completion percentage.
  const dialog = document.querySelector('#scan-dialog');
  if (dialog) {
    const elapsed=document.createElement('p');elapsed.className='scan-elapsed';
    dialog.querySelector('.scan-dialog-head')?.after(elapsed);
    let timer=null,started=0;
    function elapsedState(){
      const active=dialog.open&&!['succeeded','failed','expired','cancelled'].includes(dialog.dataset.status);
      if(active&&!timer){started=Date.now();elapsed.textContent='本次窗口已打开 0 秒';timer=setInterval(()=>{elapsed.textContent=`本次窗口已打开 ${Math.floor((Date.now()-started)/1000)} 秒 · 验证需本人完成`;},1000);}
      if(!active&&timer){clearInterval(timer);timer=null;}
    }
    new MutationObserver(elapsedState).observe(dialog,{attributes:true,attributeFilter:['open','data-status']});elapsedState();
    const progress = document.createElement('ol');progress.className='scan-progress';progress.setAttribute('aria-label','绑定进度');
    for (const label of ['连接登录页','扫码或验证','保存账号']) {const li=document.createElement('li');li.textContent=label;progress.append(li);}
    dialog.querySelector('.scan-dialog-head')?.after(progress);
    function update() {
      const state=dialog.dataset.status;
      const index=state==='succeeded'?2:['awaiting_scan','confirming'].includes(state)||dialog.dataset.frameReady==='true'?1:0;
      [...progress.children].forEach((li,i)=>{li.dataset.complete=String(i<index||state==='succeeded');if(i===index)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current');});
      progress.hidden=['failed','expired','cancelled'].includes(state);
    }
    new MutationObserver(update).observe(dialog,{attributes:true,attributeFilter:['data-status','data-frame-ready']});update();
  }
})();
