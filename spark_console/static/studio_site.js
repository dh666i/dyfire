(() => {
  const root=document.documentElement;
  function theme(value){root.dataset.theme=value;root.style.colorScheme=value;document.querySelector('meta[name="theme-color"]')?.setAttribute('content',value==='light'?'#e9e7e1':'#111110');document.querySelectorAll('[data-studio-theme]').forEach(b=>b.setAttribute('aria-label',value==='light'?'切换到深色模式':'切换到浅色模式'));}
  theme(root.dataset.theme||'dark');
  document.querySelectorAll('[data-studio-theme]').forEach(button=>button.onclick=()=>{const next=root.dataset.theme==='dark'?'light':'dark';theme(next);try{localStorage.setItem('studio-theme',next)}catch{}});
  document.addEventListener('click',event=>{document.querySelectorAll('.studio-profile[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.studio-profile[open]').forEach(menu=>menu.open=false);});
  window.addEventListener('storage',event=>{if(event.key==='studio-theme')theme(event.newValue==='light'?'light':'dark');});
  // Give every data table readable mobile labels without changing its controls.
  function labelTables(){document.querySelectorAll('table').forEach(table=>{const headers=[...table.querySelectorAll('thead th')].map(h=>h.textContent.trim());table.querySelectorAll('tbody tr').forEach(row=>[...row.children].forEach((cell,i)=>{if(!cell.hasAttribute('colspan')&&headers[i])cell.dataset.label=headers[i];}));});}
  labelTables();const groups=document.querySelector('.admin-task-groups');if(groups)new MutationObserver(labelTables).observe(groups,{childList:true,subtree:true});
})();
