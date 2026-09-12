(() => {
  'use strict';
  if (window.sparkDesignSystem) return;
  const storage = {get(key, fallback) {try {return JSON.parse(sessionStorage.getItem(key)) ?? fallback;} catch {return fallback;}},set(key,value) {try {sessionStorage.setItem(key,JSON.stringify(value));} catch {}}};
  const cardSelector='.panel:not(.task-list-panel):not(.task-create-panel):not(.account-list):not(.help-sections),.admin-panel,.account-item,.studio-identity-card,.subscription-summary,.subscription-redeem';
  const menus='.studio-profile,.profile-menu,.row-menu';
  const enhanced=new WeakSet(),dialogs=new WeakSet();
  let openMenu=null;
  function positionMenu(menu) {
    const panel=menu.querySelector('.studio-profile-menu,.profile-popover,.row-menu-panel'),trigger=menu.querySelector('summary');
    if (!panel||!trigger||!menu.open) return;
    const rect=trigger.getBoundingClientRect(),height=window.visualViewport?.height||innerHeight;
    panel.style.setProperty('--menu-height',Math.max(100,height-24)+'px');
    const bounds=panel.getBoundingClientRect(),below=height-rect.bottom-12,above=rect.top-12;
    const maxHeight=Math.max(100,Math.max(below,above)-8);
    panel.style.setProperty('--menu-height',maxHeight+'px');
    panel.style.setProperty('--menu-x',Math.max(12,Math.min(innerWidth-bounds.width-12,rect.right-bounds.width))+'px');
    panel.style.setProperty('--menu-y',Math.max(12,below>=Math.min(bounds.height,maxHeight)||below>=above?rect.bottom+8:rect.top-Math.min(bounds.height,maxHeight)-8)+'px');
  }
  function wireMenu(menu) {
    if(enhanced.has(menu))return;enhanced.add(menu);
    const panel=menu.querySelector('.studio-profile-menu,.profile-popover,.row-menu-panel'),trigger=menu.querySelector('summary');if(!panel||!trigger)return;
    // Top layer prevents parent card blur / overflow from clipping action menus.
    if(typeof panel.showPopover==='function')panel.setAttribute('popover','manual');
    trigger.setAttribute('aria-expanded',String(menu.open));
    menu.addEventListener('toggle',()=>{
      trigger.setAttribute('aria-expanded',String(menu.open));
      if(menu.open){if(openMenu&&openMenu!==menu)openMenu.open=false;openMenu=menu;if(panel.showPopover&&!panel.matches(':popover-open'))panel.showPopover();positionMenu(menu);}
      else{if(panel.hidePopover&&panel.matches(':popover-open'))panel.hidePopover();if(openMenu===menu)openMenu=null;}
    });
    menu.addEventListener('keydown',event=>{
      if(event.key==='Escape'){menu.open=false;trigger.focus();event.stopPropagation();}
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      event.preventDefault();menu.open=true;
      const links=[...panel.querySelectorAll('a[href],button:not(:disabled)')],index=links.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?links.length-1:event.key==='ArrowDown'?(index+1)%links.length:(index<=0?links.length:index)-1;
      links[next]?.focus();
    });
  }
  function wireDialog(dialog) {
    if(dialogs.has(dialog))return;dialogs.add(dialog);let origin=null;
    const heading=dialog.querySelector('h2');if(!dialog.hasAttribute('aria-label')&&!dialog.hasAttribute('aria-labelledby')&&heading){heading.id ||= 'dialog-heading-'+(++wireDialog.index);dialog.setAttribute('aria-labelledby',heading.id);}
    new MutationObserver(()=>{if(dialog.open){origin=lastTrigger;dialog.querySelector('input:not([type=hidden]),button')?.setAttribute('data-dialog-initial','');}else if(origin?.isConnected&&!document.querySelector('dialog[open]'))origin.focus({preventScroll:true});}).observe(dialog,{attributes:true,attributeFilter:['open']});
  }
  wireDialog.index=0;let lastTrigger=null;
  function enhance(root=document) {
    const collect=selector=>[...(root.matches?.(selector)?[root]:[]),...root.querySelectorAll(selector)];
    for(const card of collect(cardSelector)){
      if(card.classList.contains('ui-card'))continue;
      card.classList.add('ui-card');
    }
    collect(menus).forEach(wireMenu);collect('dialog').forEach(wireDialog);
    collect('img[data-avatar-image],.avatar img,.account-avatar img,.directory-avatar img').forEach(img=>{if(enhanced.has(img))return;enhanced.add(img);img.addEventListener('error',()=>img.remove(),{once:true});});
  }
  function createDrawer(panel,form,host,triggerSelector) {
    if(!panel||!form||!host||typeof HTMLDialogElement.prototype.showModal!=='function')return;
    const dialog=document.createElement('dialog');dialog.className='ui-create-drawer';dialog.setAttribute('aria-label','新建续火任务');
    const header=document.createElement('div');header.className='ui-drawer-head';header.innerHTML='<h2>新建续火任务</h2><button type="button" aria-label="关闭新建任务">关闭</button>';
    dialog.append(header);document.body.append(dialog);dialog.append(panel);host.classList.add('ui-has-drawer');
    let dirty=false,origin=null;
    form.addEventListener('input',()=>{dirty=true;});form.addEventListener('change',()=>{dirty=true;});
    form.addEventListener('reset',()=>{dirty=false;});
    function open(trigger){origin=trigger||document.activeElement;if(!dialog.open)dialog.showModal();(form.querySelector('select')?.parentElement.querySelector('.ui-select-trigger')||form.querySelector('select,input:not([type=hidden])'))?.focus();}
    function close(){
      if(form.dataset.previewBusy||form.dataset.submitting==='true'||form.querySelector('[aria-busy=true]')){window.sparkToast?.('正在处理，请等待结果后关闭');return;}
      if(dirty&&!dialog.querySelector('.ui-drawer-dirty')){
        const notice=document.createElement('div');notice.className='ui-drawer-dirty';notice.setAttribute('role','status');notice.innerHTML='<p>内容尚未提交。暂时关闭后，本页内仍保留填写内容。</p><div><button type="button">继续填写</button><button type="button" class="quiet">暂时关闭</button></div>';header.after(notice);
        const buttons=notice.querySelectorAll('button');buttons[0].onclick=()=>notice.remove();buttons[1].onclick=()=>{notice.remove();dialog.close();origin?.focus();};buttons[0].focus();return;
      }
      if(dirty)return;dialog.close();origin?.focus();
    }
    header.querySelector('button').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    document.addEventListener('click',event=>{const trigger=event.target.closest(triggerSelector);if(trigger){event.preventDefault();open(trigger);}},true);
    document.addEventListener('task-created',()=>{dirty=false;dialog.close();origin?.focus();});
    if(location.hash==='#new-task')open();
    return {open,close,dialog};
  }
  function init() {
    enhance();
    const panel=document.querySelector('#new-task'),form=panel?.querySelector('form[data-task-create-form]');
    if(form)createDrawer(panel,form,document.querySelector('.task-workbench'),'[data-open-task-create]');
    document.addEventListener('pointerdown',event=>{lastTrigger=event.target.closest('button,a,summary,input')||document.activeElement;},{passive:true});
    document.addEventListener('click',event=>{if(openMenu&&!openMenu.contains(event.target))openMenu.open=false;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&openMenu){const trigger=openMenu.querySelector('summary');openMenu.open=false;trigger?.focus();}});
    window.addEventListener('resize',()=>{if(openMenu)positionMenu(openMenu);},{passive:true});
    window.addEventListener('scroll',()=>{if(openMenu)positionMenu(openMenu);},{passive:true,capture:true});
    document.addEventListener('invalid',event=>{const field=event.target;field.setAttribute('aria-invalid','true');const message=field.validationMessage;if(message)window.sparkToast?.(message,'error');},true);
    document.addEventListener('input',event=>{if(event.target.matches('input,select,textarea')&&event.target.validity?.valid)event.target.removeAttribute('aria-invalid');});
    document.querySelectorAll('a[href="/studio?view=overview"]').forEach(link=>link.href='/dashboard');
    // Store only navigation position, never account secrets or entered form values.
    const positionKey='ui-scroll:'+location.pathname+location.search;
    window.addEventListener('pagehide',()=>storage.set(positionKey,scrollY));
    const nav=performance.getEntriesByType('navigation')[0];if(nav?.type==='back_forward')requestAnimationFrame(()=>scrollTo(0,storage.get(positionKey,0)));
    new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node.nodeType===1&&!node.classList.contains('ui-card-light'))enhance(node);}).observe(document.body,{childList:true,subtree:true});
  }
  window.sparkDesignSystem={enhance,createDrawer,storage};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
