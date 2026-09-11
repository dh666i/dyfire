(() => {
  'use strict';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
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
