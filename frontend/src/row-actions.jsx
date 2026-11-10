import React, {useRef,useState} from 'react';
import {Menu} from '@base-ui/react/menu';
import {StatusIcon} from './ui-icons.jsx';

export default function RowActions({label,items}) {
  const lock=useRef(false), [busy,setBusy]=useState(false);
  const activate=async item=>{
    if(lock.current||item.disabled)return;
    lock.current=true;setBusy(true);
    try {await item.onClick?.()}finally{lock.current=false;setBusy(false)}
  };
  return <Menu.Root><Menu.Trigger className="row-menu-trigger" disabled={busy} aria-busy={busy||undefined} aria-label={label}><StatusIcon kind={busy?'loading':'more'}/></Menu.Trigger>
    <Menu.Portal><Menu.Positioner className="row-menu-positioner" sideOffset={6} align="end"><Menu.Popup className="row-menu-popup">
      {items.map(item=><Menu.Item key={item.label} disabled={busy||item.disabled} onClick={()=>activate(item)} className={`row-menu-item${item.danger?' is-danger':''}`}>{item.label}</Menu.Item>)}
    </Menu.Popup></Menu.Positioner></Menu.Portal>
  </Menu.Root>;
}
