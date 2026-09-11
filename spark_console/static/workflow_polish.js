(() => {
  // Native details retains keyboard support and works without JavaScript.
  const menus=[...document.querySelectorAll('.row-menu')];
  menus.forEach(menu=>menu.addEventListener('toggle',()=>{if(menu.open)menus.forEach(other=>{if(other!==menu)other.open=false;});}));
  document.addEventListener('pointerdown',event=>menus.forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
  document.addEventListener('keydown',event=>{if(event.key==='Escape')menus.forEach(menu=>{if(menu.open){menu.open=false;menu.querySelector('summary').focus();}});});
  document.addEventListener('focusin',event=>menus.forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));

  const mail=location.pathname==='/admin/settings/mail';
  const subscription=location.pathname==='/subscription';
  if(!mail&&!subscription)return;
  const tables=mail?document.querySelectorAll('.mail-workspace .admin-table'):document.querySelectorAll('.admin-table');
  tables.forEach(table=>{
    const wrap=table.closest('.admin-table-wrap');if(!wrap)return;
    const labels=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
    const records=document.createElement('div');records.className='mobile-records';records.setAttribute('aria-label',mail?'邮件发送记录':'会员开通记录');
    const rows=[...table.querySelectorAll('tbody tr')];
    const pairs=[];
    for(const row of rows){
      const cells=[...row.cells].map(td=>td.textContent.trim());
      if(cells.length!==labels.length){const empty=document.createElement('p');empty.textContent=cells[0];records.append(empty);continue;}
      const detail=document.createElement('details');detail.className='mobile-record';
      const summary=document.createElement('summary');
      const title=document.createElement('strong');title.textContent=mail?cells[1]:cells[1];
      const when=document.createElement('small');when.textContent=cells[0];
      const state=document.createElement('span');state.textContent=mail?cells[3]:'查看详情';state.className='record-summary-state';
      if(mail)state.dataset.tone=cells[3]==='服务器已接受'?'good':cells[3]==='失败'?'error':cells[3]==='结果待核对'?'warn':'neutral';
      summary.append(title,state,when);detail.append(summary);
      const facts=document.createElement('dl');
      cells.forEach((value,index)=>{const item=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=labels[index];dd.textContent=value;item.append(dt,dd);facts.append(item);});
      detail.append(facts);records.append(detail);pairs.push({row,detail,cells});
    }
    wrap.classList.add('responsive-record-table');wrap.after(records);
    if(mail&&pairs.length){
      const toolbar=document.createElement('div');toolbar.className='record-tools';
      const search=document.createElement('input');search.type='search';search.placeholder='搜索收件邮箱或通知';search.setAttribute('aria-label','搜索邮件发送记录');
      const status=document.createElement('select');status.setAttribute('aria-label','邮件结果筛选');status.add(new Option('全部结果',''));
      [...new Set(pairs.map(p=>p.cells[3]))].forEach(value=>status.add(new Option(value,value)));
      const count=document.createElement('span');count.setAttribute('role','status');
      const empty=document.createElement('p');empty.textContent='没有匹配的记录，请调整筛选条件。';empty.hidden=true;
      const reset=document.createElement('button');reset.type='button';reset.className='quiet';reset.textContent='清除筛选';
      const apply=()=>{let shown=0;for(const p of pairs){const matches=p.cells.join(' ').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())&&(!status.value||status.value===p.cells[3]);p.row.hidden=p.detail.hidden=!matches;if(matches)shown++;}count.textContent=`显示 ${shown} / ${pairs.length} 条`;empty.hidden=shown>0;};
      search.addEventListener('input',apply);status.addEventListener('change',apply);reset.addEventListener('click',()=>{search.value='';status.value='';status.dispatchEvent(new Event('change',{bubbles:true}));search.focus();});
      toolbar.append(search,status,reset,count);wrap.before(toolbar);records.after(empty);apply();
    }
  });
})();
