// The native select remains the form value; this popup only owns presentation.
export function installSelectOverlay(windowObject=window) {
  if (windowObject.__dyfireSelectOverlay) return windowObject.__dyfireSelectOverlay;
  const document=windowObject.document;
  const disabled=option=>!option||option.disabled||(option.parentElement?.tagName==='OPTGROUP'&&option.parentElement.disabled);
  const enhanced=target=>{
    const select=target?.closest?.('select');
    return select&&!select.disabled&&!select.multiple&&select.size<=1?select:null;
  };
  let active=null, menu=null, activeIndex=-1, sequence=0;
  let activation=null;
  const justActivated=select=>activation?.select===select&&Date.now()-activation.at<1000;
  const close=({restoreFocus=false}={})=>{
    const select=active;
    if(menu){const exiting=menu;exiting.classList.add('is-closing');exiting.setAttribute('aria-hidden','true');exiting.inert=true;
      if(!windowObject.matchMedia || windowObject.matchMedia('(prefers-reduced-motion: reduce)').matches)exiting.remove();
      else windowObject.setTimeout(()=>exiting.remove(),140);
    } menu=null; active=null; activeIndex=-1;
    if(select){
      select.removeAttribute('aria-expanded'); select.removeAttribute('aria-controls');
      select.removeAttribute('aria-activedescendant');
      if(restoreFocus&&select.isConnected)select.focus({preventScroll:true});
    }
  };
  const highlight=index=>{
    if(!active||!menu||!Number.isInteger(index)||index<0||disabled(active.options[index]))return;
    activeIndex=index;
    menu.querySelectorAll('.fire-select-option').forEach((row,i)=>row.classList.toggle('is-active',i===index));
    active.setAttribute('aria-activedescendant',`${menu.id}-${index}`);
    menu.children[index]?.scrollIntoView?.({block:'nearest',behavior:'instant'});
  };
  const enabledIndices=()=>[...active.options].map((option,i)=>disabled(option)?-1:i).filter(i=>i>=0);
  const move=direction=>{
    const indices=enabledIndices();
    const candidates=indices.filter(i=>direction>0?i>activeIndex:i<activeIndex);
    highlight(direction>0?candidates[0]??indices.at(-1):candidates.at(-1)??indices[0]);
  };
  const choose=index=>{
    const select=active;
    if(!select||select.disabled||disabled(select.options[index]))return;
    const changed=select.selectedIndex!==index;
    select.selectedIndex=index;
    close({restoreFocus:true});
    if(changed){
      select.dispatchEvent(new windowObject.Event('input',{bubbles:true}));
      select.dispatchEvent(new windowObject.Event('change',{bubbles:true}));
    }
  };
  const open=select=>{
    if(select.disabled||select.multiple||select.size>1)return;
    close();active=select;select.focus({preventScroll:true});
    const rect=select.getBoundingClientRect();
    menu=document.createElement('div');menu.className='fire-select-menu';menu.id=`fire-select-${++sequence}`;
    menu.setAttribute('role','listbox');
    menu.setAttribute('aria-label',select.getAttribute('aria-label')||select.labels?.[0]?.textContent?.trim()||'选择');
    [...select.options].forEach((option,index)=>{
      const row=document.createElement('button');row.type='button';row.tabIndex=-1;
      row.className='fire-select-option';row.id=`${menu.id}-${index}`;
      row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.selected));row.disabled=Boolean(disabled(option));
      const label=document.createElement('span');label.textContent=option.textContent;row.append(label);
      if(option.selected){const check=document.createElement('b');check.setAttribute('aria-hidden','true');check.textContent='✓';row.append(check)}
      row.addEventListener('pointerdown',event=>event.preventDefault());
      // Inserting a popup beneath a stationary pointer can synthesize enter
      // events (notably WebKit). Only real pointer motion may take selection
      // back from the keyboard; touch commits through click instead.
      row.addEventListener('pointermove',event=>{if(event.pointerType!=='touch')highlight(index)});
      row.addEventListener('click',()=>choose(index));menu.append(row);
    });
    const below=windowObject.innerHeight-rect.bottom-12,above=rect.top-12,goUp=below<220&&above>below;
    const maxHeight=Math.max(120,Math.min(300,goUp?above:below));
    menu.dataset.direction=goUp?'up':'down';
    menu.style.width=`${rect.width}px`;menu.style.left=`${Math.max(8,Math.min(rect.left,windowObject.innerWidth-rect.width-8))}px`;
    menu.style.maxHeight=`${maxHeight}px`;menu.style.top=`${rect.bottom+7}px`;document.body.append(menu);
    if(goUp)menu.style.top=`${Math.max(8,rect.top-Math.min(menu.scrollHeight,maxHeight)-7)}px`;
    select.setAttribute('aria-expanded','true');select.setAttribute('aria-controls',menu.id);
    highlight(select.selectedIndex>=0&&!disabled(select.options[select.selectedIndex])?select.selectedIndex:enabledIndices()[0]);
  };
  const toggle=(select,source)=>{activation={select,source,at:Date.now()};active===select?close():open(select)};
  const onPointer=event=>{
    const select=enhanced(event.target);
    if(select){event.preventDefault();toggle(select,'pointer');return}
    if(menu?.contains(event.target))return;
    close();
  };
  // Safari can activate the native picker at touch/click default-action time
  // even when pointerdown was cancelled. Cancel each native entry point, but
  // let only the first event in the gesture toggle our listbox. Keep these
  // listeners non-passive so iOS can actually cancel the touch default.
  const onStart=event=>{
    const select=enhanced(event.target);
    if(!select)return;
    event.preventDefault();
    const pointerAlreadyHandled=activation?.source==='pointer'&&justActivated(select);
    if(event.type==='touchstart'?!pointerAlreadyHandled:!justActivated(select))toggle(select,event.type);
  };
  const onTouchEnd=event=>{if(enhanced(event.target))event.preventDefault()};
  const onClick=event=>{
    const select=enhanced(event.target);
    if(!select)return;
    event.preventDefault();
    // Screen readers and programmatic activation may dispatch only click.
    if(!justActivated(select))active===select?close():open(select);
    activation=null;
  };
  const onKey=event=>{
    const select=enhanced(event.target);
    if(!select)return;
    activation=null;
    if(event.key==='Tab'){close();return}
    if(event.key==='Escape'){if(menu){event.preventDefault();event.stopImmediatePropagation();close({restoreFocus:true})}return}
    if(!['Enter',' ','ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    event.preventDefault();
    if(!menu||active!==select){open(select);if(['Enter',' '].includes(event.key))return}
    if(event.key==='Enter'||event.key===' '){choose(activeIndex);return}
    if(event.key==='Home'||event.key==='End'){
      const indices=enabledIndices();highlight(event.key==='Home'?indices[0]:indices.at(-1));return;
    }
    move(event.key==='ArrowUp'?-1:1);
  };
  const onScroll=event=>{if(menu&&event.target?.nodeType&&menu.contains(event.target))return;close()};
  const onResize=()=>close();
  const touchOptions={capture:true,passive:false};
  document.addEventListener('pointerdown',onPointer,true);document.addEventListener('keydown',onKey,true);
  document.addEventListener('mousedown',onStart,true);document.addEventListener('click',onClick,true);
  document.addEventListener('touchstart',onStart,touchOptions);document.addEventListener('touchend',onTouchEnd,touchOptions);
  windowObject.addEventListener('resize',onResize);windowObject.addEventListener('scroll',onScroll,true);
  const overlay={destroy(){close();document.removeEventListener('pointerdown',onPointer,true);document.removeEventListener('keydown',onKey,true);document.removeEventListener('mousedown',onStart,true);document.removeEventListener('click',onClick,true);document.removeEventListener('touchstart',onStart,touchOptions);document.removeEventListener('touchend',onTouchEnd,touchOptions);windowObject.removeEventListener('resize',onResize);windowObject.removeEventListener('scroll',onScroll,true);delete windowObject.__dyfireSelectOverlay}};
  windowObject.__dyfireSelectOverlay=overlay;return overlay;
}
