(() => {
  "use strict";

  const date = document.querySelector("[data-studio-date]");
  if (date) {
    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "long",
    });
    const updateDate = () => { date.textContent = formatter.format(new Date()); };
    updateDate();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) updateDate();
    });
  }

  const create = document.getElementById("new-task");
  const revealCreate = (focus = false) => {
    if (!create) return;
    if (focus) create.querySelector('#task-account')?.focus();
  };
  document.querySelectorAll("[data-open-task-create]").forEach(link => {
    link.addEventListener("click", () => revealCreate(true));
  });
  if (window.location.hash === "#new-task") revealCreate();
  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#new-task") revealCreate();
  });

  const toolbar = document.querySelector("[data-task-filter-toolbar]");
  if (!toolbar) return;
  const rows = [...document.querySelectorAll("[data-task-enabled]")];
  const search = toolbar.querySelector("[data-task-search]");
  const accountFilter = toolbar.querySelector('[data-task-account-filter]');
  const filters = [...toolbar.querySelectorAll("[data-task-filter]")];
  const empty = document.querySelector("[data-task-filter-empty]");
  const count = document.querySelector("[data-task-filter-count]");
  const reset = document.querySelector("[data-task-filter-reset]");
  let mode = "all";
  const filterKey = 'spark-task-filter';
  try {
    const saved = JSON.parse(sessionStorage.getItem(filterKey) || 'null');
    if (saved) { mode = ['all','enabled','paused'].includes(saved.mode) ? saved.mode : 'all'; search.value = saved.query || ''; if(accountFilter && [...accountFilter.options].some(o=>o.value===saved.account))accountFilter.value=saved.account; }
  } catch (_) {}
  const linkedAccount=new URLSearchParams(location.search).get('account');
  if(linkedAccount&&accountFilter&&[...accountFilter.options].some(o=>o.value===linkedAccount))accountFilter.value=linkedAccount;
  let pageIndex=1;
  const options=document.createElement('div');options.className='workspace-options';
  options.innerHTML='<label>排序<select data-order><option value="time">按发送时间</option><option value="name">按好友名称</option><option value="attention">异常优先</option><option value="account">按账号分组</option></select></label><label>每页<select data-size><option value="12">12 个</option><option value="24">24 个</option><option value="48">48 个</option></select></label>';
  toolbar.after(options);
  const order=options.querySelector('[data-order]'),size=options.querySelector('[data-size]');
  try{const saved=JSON.parse(sessionStorage.getItem('spark-task-layout')||'null');if(saved){if(['time','name','attention','account'].includes(saved.order))order.value=saved.order;if(['12','24','48'].includes(saved.size))size.value=saved.size;}}catch(_){}
  const pager=document.createElement('nav');pager.className='workspace-pager';pager.setAttribute('aria-label','任务列表分页');
  pager.innerHTML='<button type="button" class="quiet" data-prev>上一页</button><span role="status"></span><button type="button" class="quiet" data-next>下一页</button>';
  document.querySelector('.task-overview')?.after(pager);
  const apply = () => {
    const query = search.value.trim().toLocaleLowerCase();
    try { sessionStorage.setItem(filterKey, JSON.stringify({mode,query:search.value,account:accountFilter?.value||''})); } catch (_) {}
    let shown = 0;const matchesRows=[];
    for (const row of rows) {
      const stateMatches = mode === "all" || row.dataset.taskEnabled === (mode === "enabled" ? "true" : "false");
      const matches = stateMatches && (!accountFilter?.value || accountFilter.value===row.dataset.taskAccount) && row.dataset.taskSearchText.toLocaleLowerCase().includes(query);
      row.hidden = !matches;
      if (matches) {shown++;matchesRows.push(row);}
    }
    const name=row=>row.querySelector('.directory-person strong')?.textContent||'';
    const time=row=>row.querySelector('.task-overview-time')?.textContent||'';
    const attention=row=>!!row.querySelector('.task-health-note,.task-overview-result .warn,.task-overview-result .off');
    matchesRows.sort((a,b)=>order.value==='name'?name(a).localeCompare(name(b),'zh-CN'):order.value==='attention'?Number(attention(b))-Number(attention(a))||time(a).localeCompare(time(b)):order.value==='account'?(a.dataset.taskAccount||'').localeCompare(b.dataset.taskAccount||'')||time(a).localeCompare(time(b)):time(a).localeCompare(time(b))||name(a).localeCompare(name(b),'zh-CN'));
    const pages=Math.max(1,Math.ceil(shown/Number(size.value)));pageIndex=Math.min(pageIndex,pages);
    const body=document.querySelector('.task-overview-body');body?.querySelectorAll('.task-account-heading').forEach(n=>n.remove());
    let previous=null;
    matchesRows.forEach((row,index)=>{row.hidden=index<(pageIndex-1)*Number(size.value)||index>=pageIndex*Number(size.value);if(!row.hidden&&order.value==='account'&&previous!==row.dataset.taskAccount){const heading=document.createElement('div');heading.className='task-account-heading';heading.setAttribute('role','row');const cell=document.createElement('span');cell.setAttribute('role','cell');cell.textContent=row.querySelector('.task-overview-account>span:last-child')?.textContent||'未绑定账号';heading.append(cell);body?.append(heading);previous=row.dataset.taskAccount;}body?.append(row);});
    pager.querySelector('span').textContent=`第 ${pageIndex} / ${pages} 页 · 共 ${shown} 个`;pager.querySelector('[data-prev]').disabled=pageIndex===1;pager.querySelector('[data-next]').disabled=pageIndex===pages;pager.hidden=rows.length===0;
    try{sessionStorage.setItem('spark-task-layout',JSON.stringify({order:order.value,size:size.value}));}catch(_){}
    filters.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.taskFilter === mode)));
    if (empty) empty.hidden = shown > 0 || rows.length === 0;
    if (count) {
      count.textContent = `显示 ${shown} / ${rows.length} 个计划`;
      count.hidden = !query && mode === "all" && !accountFilter?.value;
    }
    document.dispatchEvent(new Event('task-filter-updated'));
    const list=document.querySelector('.task-overview-body');window.sparkReveal?.(list);
  };
  order.addEventListener('change',()=>{pageIndex=1;apply();});size.addEventListener('change',()=>{pageIndex=1;apply();});
  pager.querySelector('[data-prev]').addEventListener('click',()=>{pageIndex--;apply();});pager.querySelector('[data-next]').addEventListener('click',()=>{pageIndex++;apply();});
  filters.forEach(button => button.addEventListener("click", () => {
    pageIndex=1;
    mode = button.dataset.taskFilter;
    apply();
  }));
  search.addEventListener("input", ()=>{pageIndex=1;apply();});
  accountFilter?.addEventListener('change',()=>{pageIndex=1;apply();});
  reset?.addEventListener("click", () => {
    mode = "all";
    if(accountFilter){accountFilter.value='';accountFilter.dispatchEvent(new Event('change',{bubbles:true}));}
    search.value = "";
    apply();
    search.focus();
  });
  toolbar.hidden = false;
  document.addEventListener('task-state-updated',apply);
  apply();
  accountFilter?.dispatchEvent(new Event('change',{bubbles:true}));
})();
