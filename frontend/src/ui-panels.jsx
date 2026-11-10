import React, {useEffect, useRef, useState} from 'react';
import {Drawer as Sheet} from '@base-ui/react/drawer';
import {StatusIcon} from './ui-controls.jsx';

export function useCompactViewport() {
  const [compact,setCompact]=useState(()=>window.matchMedia('(max-width: 700px)').matches);
  useEffect(()=>{
    const media=window.matchMedia('(max-width: 700px)');
    const update=()=>setCompact(media.matches);
    media.addEventListener('change',update);return()=>media.removeEventListener('change',update);
  },[]);
  return compact;
}

// Only the friend picker uses swipe dismissal. Remote browser/captcha windows
// keep their independent pointer interactions and never enter this component.
export function ContactPanel({DesktopDrawer,open,onClose,title,children,...props}) {
  const compact=useCompactViewport();
  const closeRef=useRef(null);
  const [shortViewport,setShortViewport]=useState(false);
  useEffect(()=>{
    if(!open||!compact){setShortViewport(false);return;}
    const viewport=window.visualViewport;
    // Base UI owns keyboard inset/scroll handling. This flag only reduces
    // secondary footer copy when the remaining reading area is short.
    const update=()=>{if(!viewport||viewport.scale===1)setShortViewport((viewport?.height||window.innerHeight)<540)};
    update();viewport?.addEventListener('resize',update);
    return()=>viewport?.removeEventListener('resize',update);
  },[open,compact]);
  if(!compact)return <DesktopDrawer {...props} open={open} onClose={onClose} title={title}>{children}</DesktopDrawer>;
  return <Sheet.Root open={open} onOpenChange={next=>{if(!next)onClose()}} swipeDirection="down"><Sheet.VirtualKeyboardProvider>
    <Sheet.Portal><Sheet.Backdrop className="quality-sheet-backdrop"/>
      <Sheet.Viewport className="quality-sheet-viewport"><Sheet.Popup className="drawer quality-sheet" data-short-viewport={shortViewport||undefined} initialFocus={closeRef}>
        <div className="sheet-grip" aria-hidden="true"><span/></div>
        <header className="drawer-head"><Sheet.Title>{title}</Sheet.Title>
          <Sheet.Close ref={closeRef} className="icon-button" aria-label="关闭"><StatusIcon kind="close"/></Sheet.Close>
        </header>
        <Sheet.Content className="quality-sheet-content" data-base-ui-swipe-ignore>{children}</Sheet.Content>
      </Sheet.Popup></Sheet.Viewport>
    </Sheet.Portal>
  </Sheet.VirtualKeyboardProvider></Sheet.Root>;
}
