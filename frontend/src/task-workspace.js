export function installTaskWorkspace({studio,state}) {
  if(new URLSearchParams(location.search).has('view'))return;
  const host=document.querySelector('#workspace'),list=document.querySelector('#task-list');if(!host||!list)return;
  const {storage,createDrawer}=window.sparkDesignSystem;
  const tools=document.createElement('div');tools.className='ui-workspace-actions';
  const account=document.createElement('select');account.setAttribute('aria-label','按发送账号筛选任务');account.add(new Option('全部抖音账号',''));
  state.accounts.forEach(a=>account.add(new Option(a.name,a.id)));
  const size=document.createElement('select');size.setAttribute('aria-label','每页任务数');[12,24,48].forEach(n=>size.add(new Option('每页 '+n+' 个',String(n))));
  const create=document.createElement('button');create.type='button';create.className='submit';create.dataset.openStudioCreate='';create.textContent='新建任务';
  tools.append(account,size,create);list.parentElement.querySelector('.filters').before(tools);
  const pager=document.createElement('nav');pager.className='workspace-pager';pager.setAttribute('aria-label','任务分页');pager.innerHTML='<button type="button" class="quiet">上一页</button><span role="status"></span><button type="button" class="quiet">下一页</button>';list.after(pager);
  const saved=storage.get('studio-task-layout',{}),collapsed=storage.get('studio-account-collapsed',{});
  let page=1,rows=[],revealIds=null;
  size.value=[12,24,48].includes(Number(saved.size))?String(saved.size):'24';
  account.value=state.accounts.some(a=>a.id===saved.account)?saved.account:'';
  const linked=new URLSearchParams(location.search).get('account');if(state.accounts.some(a=>a.id===linked))account.value=linked;
  const taskFor=row=>studio.tasks.find(t=>t.id===row.querySelector('[data-toggle]')?.dataset.toggle);
  function apply() {
    if(!rows.length){pager.hidden=true;return;}pager.hidden=false;
    const matches=rows.filter(row=>{const task=taskFor(row);return task&&(!account.value||task.accountId===account.value);});
    const totalPages=Math.max(1,Math.ceil(matches.length/Number(size.value)));page=Math.min(page,totalPages);
    const fragment=document.createDocumentFragment(),groups=new Map();
    matches.sort((a,b)=>{
      const ta=taskFor(a),tb=taskFor(b),blocked=id=>state.accounts.find(acc=>acc.id===id)?.health.blocked;
      return Number(!!blocked(tb.accountId))-Number(!!blocked(ta.accountId))||String(ta.accountId).localeCompare(String(tb.accountId))||ta.time.localeCompare(tb.time);
    });
    if(revealIds){const index=matches.findIndex(row=>revealIds.includes(taskFor(row)?.id));page=index<0?1:Math.floor(index/Number(size.value))+1;revealIds=null;}
    for(const row of matches.slice((page-1)*Number(size.value),page*Number(size.value))){const task=taskFor(row),id=task.accountId||'unbound';if(!groups.has(id))groups.set(id,[]);groups.get(id).push(row);}
    for(const [id,items] of groups){
      const a=state.accounts.find(a=>a.id===id),button=document.createElement('button'),body=document.createElement('div');button.type='button';button.className='ui-group-toggle';body.className='ui-group-content';
      const title=document.createElement('strong'),count=document.createElement('small');title.textContent=a?.name||'账号不可用';
      const total=matches.filter(row=>(taskFor(row)?.accountId||'unbound')===id).length;count.textContent=`${total} 个任务${a?.health.blocked?' · '+a.health.label:''}`;button.append(title,count);
      const searching=!!document.querySelector('#search').value.trim();body.hidden=!searching&&!!collapsed[id];button.setAttribute('aria-expanded',String(!body.hidden));
      button.onclick=()=>{body.hidden=!body.hidden;collapsed[id]=body.hidden;button.setAttribute('aria-expanded',String(!body.hidden));storage.set('studio-account-collapsed',collapsed);};
      body.append(...items);fragment.append(button,body);
    }
    if(!matches.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='当前账号下没有匹配的任务，请调整账号或任务筛选。';fragment.append(empty);}
    list.replaceChildren(fragment);pager.querySelector('span').textContent=`第 ${page} / ${totalPages} 页 · ${matches.length} 个任务`;
    pager.firstElementChild.disabled=page===1;pager.lastElementChild.disabled=page===totalPages;
    storage.set('studio-task-layout',{account:account.value,size:size.value});
  }
  function capture(){rows=[...list.querySelectorAll('.task-row')];page=1;apply();}
  document.addEventListener('studio:render-tasks',capture);capture();
  account.onchange=size.onchange=()=>{page=1;apply();};pager.firstElementChild.onclick=()=>{page--;apply();};pager.lastElementChild.onclick=()=>{page++;apply();};
  createDrawer(document.querySelector('.composer'),document.querySelector('#create-form'),host,'[data-open-studio-create]');
  const query=document.querySelector('#search'),savedQuery=storage.get('studio-task-query','');
  if(savedQuery){query.value=savedQuery;query.dispatchEvent(new Event('input',{bubbles:true}));}
  query.addEventListener('input',()=>storage.set('studio-task-query',query.value));
  document.addEventListener('studio:tasks-created',event=>{
    query.value='';query.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-filter="all"]').click();
    account.value=event.detail.account;collapsed[event.detail.account]=false;
    revealIds=event.detail.ids;
    apply();
  });
  const selectedOnly=document.createElement('button');selectedOnly.type='button';selectedOnly.textContent='仅看已选';selectedOnly.setAttribute('aria-pressed','false');document.querySelector('.selection-tools').append(selectedOnly);
  function filterFriends(){const only=selectedOnly.getAttribute('aria-pressed')==='true',term=document.querySelector('#friend-search').value.trim().toLocaleLowerCase();let count=0;
    document.querySelectorAll('.friend-option').forEach(row=>{row.hidden=!(row.dataset.name.includes(term)&&(!only||row.querySelector('input').checked));if(!row.hidden)count++;});
    document.querySelector('#friend-empty').hidden=count>0;
    if(!count)document.querySelector('#friend-empty').textContent=only?'没有符合条件的已选好友。':'没有匹配的已同步会话。';
  }
  selectedOnly.onclick=()=>{selectedOnly.setAttribute('aria-pressed',String(selectedOnly.getAttribute('aria-pressed')!=='true'));filterFriends();};
  document.querySelector('#friend-search').addEventListener('input',()=>{if(selectedOnly.getAttribute('aria-pressed')==='true')filterFriends();});
  document.querySelector('#friend-list').addEventListener('change',()=>{if(selectedOnly.getAttribute('aria-pressed')==='true')filterFriends();});
  document.querySelector('#clear-selection').addEventListener('click',()=>{if(selectedOnly.getAttribute('aria-pressed')==='true')filterFriends();});
}
