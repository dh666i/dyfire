import React, {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Menu} from '@base-ui/react/menu';
import {StatusIcon} from './ui-icons.jsx';

// All row menus live outside animated/scrolling rows. Base UI owns keyboard,
// dismissal, collision handling and focus return; no row-specific z-index.
export function RowActionMenu({label,children,busy=false,open,onOpenChange,
  triggerClassName='',popupClassName='',keepMounted=false}) {
  const [localOpen,setLocalOpen]=useState(false),[visited,setVisited]=useState(false);
  const [bottomPadding,setBottomPadding]=useState(10);
  const popup=useRef(null);
  const isOpen=open??localOpen;
  const change=(value,details)=>{setLocalOpen(value);onOpenChange?.(value,details)};
  const changeRef=useRef(change);changeRef.current=change;
  useLayoutEffect(()=>{
    if(!isOpen)return;
    setVisited(true);
    const dock=document.querySelector('.mobile-dock');
    const bounds=dock?.getBoundingClientRect();
    setBottomPadding(bounds?.height?Math.max(10,window.innerHeight-bounds.top+8):10);
  },[isOpen]);
  useEffect(()=>{
    if(!isOpen)return;
    const close=event=>{
      if(event.type==='scroll'&&popup.current?.contains(event.target))return;
      changeRef.current(false);
    };
    document.addEventListener('scroll',close,true);
    window.addEventListener('resize',close);
    return()=>{document.removeEventListener('scroll',close,true);window.removeEventListener('resize',close)};
  },[isOpen]);
  return <Menu.Root open={isOpen} onOpenChange={change} modal={false}>
    <Menu.Trigger className={`row-menu-trigger ${triggerClassName}`.trim()} disabled={busy}
      aria-busy={busy||undefined} aria-label={label} onClick={event=>event.stopPropagation()}>
      <StatusIcon kind={busy?'loading':'more'}/>
    </Menu.Trigger>
    <Menu.Portal keepMounted={keepMounted}><Menu.Positioner className="row-menu-positioner" sideOffset={6}
      align="end" collisionPadding={{top:10,left:10,right:10,bottom:bottomPadding}}>
      <Menu.Popup ref={popup} className={`row-menu-popup ${popupClassName}`.trim()}
        onClick={event=>event.stopPropagation()} onKeyDown={event=>event.stopPropagation()}>
        {(isOpen||(keepMounted&&visited))&&children}
      </Menu.Popup>
    </Menu.Positioner></Menu.Portal>
  </Menu.Root>;
}

export const RowActionItem=Menu.Item;

export default function RowActions({label,items}) {
  const lock=useRef(false), [busy,setBusy]=useState(false);
  const activate=async item=>{
    if(lock.current||item.disabled)return;
    lock.current=true;setBusy(true);
    try {await item.onClick?.()}finally{lock.current=false;setBusy(false)}
  };
  return <RowActionMenu label={label} busy={busy}>
      {items.map(item=><Menu.Item key={item.label} disabled={busy||item.disabled} onClick={()=>activate(item)} className={`row-menu-item${item.danger?' is-danger':''}`}>{item.label}</Menu.Item>)}
  </RowActionMenu>;
}
