import React, {useEffect, useId, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {focusTrapTarget} from './navigation.js';
import {StatusIcon} from './ui-controls.jsx';
const drawerStack=[];
const DRAWER_EXIT_MS=180;
const motionExitDelay=duration=>window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?1:duration;

function revealFocusedField(panel, viewport) {
  const field=document.activeElement;
  if(!panel.contains(field)||!field.matches?.('input,textarea,select,[contenteditable="true"]'))return;
  const header=panel.querySelector('.drawer-head')?.getBoundingClientRect();
  const footer=panel.querySelector(':scope > .drawer-foot,:scope > .customer-workspace-footer')?.getBoundingClientRect();
  const visibleTop=Math.max(viewport.offsetTop,header?.bottom||0)+10;
  const visibleBottom=Math.min(viewport.offsetTop+viewport.height,footer?.top??Infinity)-10;
  // Never scroll the document behind a modal. Only reveal the field inside
  // the drawer's own scroll containers, including nested verification forms.
  for(let node=field.parentElement;node&&panel.contains(node);node=node.parentElement){
    const style=getComputedStyle(node);
    if(!/(auto|scroll)/.test(style.overflowY)||node.scrollHeight<=node.clientHeight)continue;
    const region=node.getBoundingClientRect(), rect=field.getBoundingClientRect();
    const top=Math.max(visibleTop,region.top+10), bottom=Math.min(visibleBottom,region.bottom-10);
    if(bottom<=top)continue;
    const shift=rect.height>bottom-top?rect.top-top:rect.bottom>bottom?rect.bottom-bottom:rect.top<top?rect.top-top:0;
    if(Math.abs(shift)>1)node.scrollTop+=shift;
  }
}

export function Drawer({open, title, eyebrow, onClose, canClose, children, footer, wide=false, centered=false, fullScreen=false, size=''}) {
  const closeRef=useRef(null);
  const dialogRef=useRef(null);
  const restoreRef=useRef(null);
  const onCloseRef=useRef(onClose);
  const canCloseRef=useRef(canClose);
  const checkingCloseRef=useRef(false);
  canCloseRef.current=canClose;
  const closeTimerRef=useRef(null);
  const requestCloseRef=useRef(null);
  const titleId=useId().replace(/:/g,'');
  const drawerId=useId();
  const [mounted,setMounted]=useState(open);
  const [closing,setClosing]=useState(false);
  useEffect(()=>{onCloseRef.current=onClose},[onClose]);
  useEffect(()=>()=>{
    if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);
  },[]);
  useEffect(()=>{
    if(open){
      if(closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
      setMounted(true); setClosing(false); return;
    }
    if(!mounted)return;
    setClosing(true);
    closeTimerRef.current=window.setTimeout(()=>{setMounted(false);setClosing(false)},motionExitDelay(DRAWER_EXIT_MS));
    return ()=>{if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current)};
  },[open,mounted]);
  useEffect(()=>{
    // Keep the body lock, focus trap and stack entry for the entire exit
    // transition.  Tying cleanup to `open` made the sheet disappear from the
    // accessibility tree while it was still visibly animating.
    if(!mounted) return;
    drawerStack.push(drawerId);
    document.body.classList.add('drawer-open');
    restoreRef.current=document.activeElement;
    const previous=document.body.style.overflow;
    const lockCount=Number(document.body.dataset.dyfireDrawerLocks||0);
    if(lockCount===0) document.body.dataset.dyfireDrawerPreviousOverflow=previous;
    document.body.dataset.dyfireDrawerLocks=String(lockCount+1);
    document.body.style.overflow='hidden';
    const onKey=e=>{
      if(drawerStack[drawerStack.length-1]!==drawerId)return;
      if(document.querySelector('.modal-overlay'))return;
      if(e.key==='Escape'){requestCloseRef.current?.();return;}
      if(e.key!=='Tab')return;
      const dialog=dialogRef.current;
      if(!dialog)return;
      const focusable=[...dialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
        .filter(element=>{const style=window.getComputedStyle(element);return element.getAttribute('aria-hidden')!=='true'&&style.display!=='none'&&style.visibility!=='hidden';});
      const next=focusTrapTarget({key:e.key,shiftKey:e.shiftKey,activeElement:document.activeElement,focusable,contains:element=>dialog.contains(element)});
      if(next){e.preventDefault();next.focus({preventScroll:true});}
    };
    window.addEventListener('keydown',onKey);
    const focusTimer=window.setTimeout(()=>closeRef.current?.focus({preventScroll:true}),0);
    return ()=>{
      window.clearTimeout(focusTimer);
      const remaining=Math.max(0,Number(document.body.dataset.dyfireDrawerLocks||1)-1);
      if(remaining>0) document.body.dataset.dyfireDrawerLocks=String(remaining);
      else {
        const original=document.body.dataset.dyfireDrawerPreviousOverflow||previous;
        delete document.body.dataset.dyfireDrawerLocks;
        delete document.body.dataset.dyfireDrawerPreviousOverflow;
        document.body.style.overflow=original;
        document.body.classList.remove('drawer-open');
      }
      const stackIndex=drawerStack.lastIndexOf(drawerId);
      if(stackIndex>=0)drawerStack.splice(stackIndex,1);
      window.removeEventListener('keydown',onKey);restoreRef.current?.focus?.({preventScroll:true});
    };
  },[mounted,drawerId]);
  useEffect(()=>{
    if(!mounted)return;
    const viewport=window.visualViewport;
    const panel=dialogRef.current;
    const backdrop=panel?.parentElement;
    if(!viewport||!backdrop)return;
    const compact=window.matchMedia('(max-width:700px), (pointer:coarse) and (max-width:1100px)');
    let frame;
    const update=()=>{
      cancelAnimationFrame(frame);
      frame=requestAnimationFrame(()=>{
        if(!compact.matches){backdrop.style.height='';backdrop.style.top='';backdrop.style.bottom='';panel.style.removeProperty('--visible-height');return;}
        // Pinch zoom changes visualViewport too; do not fight the user's
        // zoom/pan or treat the magnified viewport as a software keyboard.
        if(viewport.scale!==1)return;
        backdrop.style.height=`${viewport.height}px`;
        backdrop.style.top=`${viewport.offsetTop}px`;
        backdrop.style.bottom='auto';
        panel.style.setProperty('--visible-height',`${viewport.height}px`);
        if(drawerStack[drawerStack.length-1]===drawerId&&!document.querySelector('.modal-overlay'))revealFocusedField(panel,viewport);
      });
    };
    update();viewport.addEventListener('resize',update);viewport.addEventListener('scroll',update);
    panel.addEventListener('focusin',update);compact.addEventListener('change',update);
    return()=>{cancelAnimationFrame(frame);viewport.removeEventListener('resize',update);viewport.removeEventListener('scroll',update);panel.removeEventListener('focusin',update);compact.removeEventListener('change',update)};
  },[mounted,drawerId]);
  const requestClose=async()=>{
    if(closing||checkingCloseRef.current)return;
    checkingCloseRef.current=true;
    try {
      if(canCloseRef.current&&!await canCloseRef.current())return;
    }finally{checkingCloseRef.current=false;}
    setClosing(true);
    if(closeTimerRef.current)window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current=window.setTimeout(()=>{
      setMounted(false);
      setClosing(false);
      onCloseRef.current?.();
    },motionExitDelay(DRAWER_EXIT_MS));
  };
  requestCloseRef.current=requestClose;
  if (!mounted) return null;
  const drawer = <div className={`drawer-backdrop ${centered?'drawer-centered-backdrop':''} ${closing?'is-closing':''}`} onMouseDown={e=>e.target===e.currentTarget&&requestClose()}><aside ref={dialogRef} className={`drawer ${wide?'drawer-wide':''} ${centered?'drawer-centered':''} ${fullScreen?'drawer-fullscreen':''} ${size?`drawer-${size}`:''} ${closing?'is-closing':''}`} role="dialog" aria-modal="true" aria-labelledby={titleId}><header className="drawer-head"><div><h2 id={titleId}>{title}</h2></div><button ref={closeRef} type="button" className="icon-button" onClick={requestClose} aria-label="关闭"><StatusIcon kind="close"/></button></header>{children}{typeof footer==='function'?footer(requestClose):footer}</aside></div>;
  return createPortal(drawer, document.body);
}
