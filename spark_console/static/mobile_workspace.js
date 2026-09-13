(() => {
  'use strict';
  const small=matchMedia('(max-width:680px)');
  function install(){
    if(!document.querySelector('form[action="/logout"]')||document.querySelector('.mobile-dock'))return false;
    document.body.classList.add('has-mobile-dock');
    const dock=document.createElement('nav');dock.className='mobile-dock';dock.setAttribute('aria-label','移动端主导航');
    const routes=[['/dashboard','概览','M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'],['/accounts','账号','M6 3h12v18H6zM10 17h4'],['/studio','任务','M4 5h16v16H4zM8 3v4M16 3v4M4 11h16M8 15h3'],['/account-center','我的','M8 7a4 4 0 1 0 8 0a4 4 0 1 0-8 0M4 21v-2a8 6 0 0 1 16 0v2']];
    const path=location.pathname;
    const current=path==='/dashboard'?'概览':path.startsWith('/accounts')?'账号':path==='/studio'||path.startsWith('/tasks')?'任务':path.startsWith('/runs')?'': '我的';
    for(const [href,label,d] of routes){const a=document.createElement('a');a.href=href;a.innerHTML=`<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="${d}"/></svg><span>${label}</span>`;if(current===label)a.setAttribute('aria-current','page');dock.append(a);}
    document.body.append(dock);
    let baseline=innerHeight;
    const keyboard=()=>{const input=document.activeElement?.matches('input:not([type=checkbox]):not([type=radio]),textarea');const height=window.visualViewport?.height||innerHeight;if(!input)baseline=innerHeight;document.body.classList.toggle('mobile-keyboard-open',!!input&&height<baseline*.75);};
    window.visualViewport?.addEventListener('resize',keyboard);document.addEventListener('focusin',keyboard);document.addEventListener('focusout',()=>setTimeout(keyboard,0));
    document.querySelectorAll('.studio-brand,.brand').forEach(a=>{a.href='/dashboard';a.setAttribute('aria-label','火花，返回概览');});
    return true;
  }
  function filters(){
    const form=document.querySelector('form.records-filter');if(!form)return;
    const marker=document.createComment('records-filter-position');form.before(marker);
    const quick=document.createElement('form');quick.className='mobile-record-search';quick.setAttribute('aria-label','搜索执行记录');
    const search=document.createElement('input');search.type='search';search.placeholder='搜索目标好友';search.setAttribute('aria-label','搜索目标好友');search.value=form.elements.q.value;
    const searchButton=document.createElement('button');searchButton.type='submit';searchButton.className='quiet';searchButton.textContent='搜索';
    const open=document.createElement('button');open.type='button';open.className='quiet';open.textContent='筛选';quick.append(search,searchButton,open);form.before(quick);
    const summary=document.createElement('div');summary.className='mobile-filter-summary';quick.after(summary);
    const query=new URLSearchParams(location.search),labels=[];
    for(const [name,label] of [['account_id','账号'],['status','状态'],['start','从'],['end','至']]){const value=query.get(name);if(!value)continue;const control=form.elements[name];const text=control?.tagName==='SELECT'?[...control.options].find(o=>o.value===value)?.textContent:value;labels.push(label+'：'+(text||value));}
    if(labels.length){const text=document.createElement('span');text.textContent=labels.join(' · ');const clear=document.createElement('a');clear.href='/runs';clear.textContent='清除筛选';summary.append(text,clear);open.textContent=`筛选 (${labels.length})`;}
    const dialog=document.createElement('dialog');dialog.className='mobile-filter-dialog';dialog.setAttribute('aria-label','筛选执行记录');
    const head=document.createElement('div');head.className='mobile-filter-heading';head.innerHTML='<h2>筛选执行记录</h2><button type="button" class="quiet">取消</button>';dialog.append(head);document.body.append(dialog);
    let snapshot=[],submitted=false;
    open.onclick=()=>{snapshot=[...form.elements].filter(e=>e.name).map(e=>[e,e.value]);submitted=false;form.elements.q.value=search.value;dialog.showModal();};
    head.querySelector('button').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{if(!submitted){snapshot.forEach(([e,value])=>{e.value=value;e.dispatchEvent(new Event('change',{bubbles:true}));});}open.focus({preventScroll:true});});
    form.addEventListener('submit',()=>{submitted=true;});
    quick.onsubmit=e=>{e.preventDefault();form.elements.q.value=search.value;form.requestSubmit();};
    const layout=()=>{quick.hidden=summary.hidden=!small.matches;if(small.matches){dialog.append(form);}else{if(dialog.open)dialog.close();marker.after(form);}};
    small.addEventListener('change',layout);layout();
  }
  function start(){filters();if(!install()&&location.pathname==='/studio'){const observer=new MutationObserver(()=>{if(install())observer.disconnect();});observer.observe(document.body,{childList:true,subtree:true});}}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
