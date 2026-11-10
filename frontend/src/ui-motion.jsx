import React, {useLayoutEffect, useRef, useState} from 'react';
import {motion, useIsPresent, useReducedMotion} from 'motion/react';
import {reducedWorkflowMotion} from './workflow-motion.jsx';

export function TabTransitionPanel({children}) {
  const present=useIsPresent(), reduced=useReducedMotion();
  return <motion.div className="customer-tab-content" inert={!present} aria-hidden={!present}
    initial={{opacity:0,y:reduced?0:10}} animate={{opacity:1,y:0}} exit={{opacity:0,y:reduced?0:-6}}
    transition={{duration:reduced?0:(present ? .24 : .12),ease:[.22,1,.36,1]}}>{children}</motion.div>;
}

// Content is visible by default. Observation only adds a one-time entrance;
// failed observers, restored scroll positions and reduced motion never hide it.
export function useViewportReveal(rootRef, scopeKey, {scrollRoot=false}={}) {
  useLayoutEffect(()=>{
    const root=rootRef.current;
    if(!root)return;
    const seen=new WeakSet(), animations=new Map();
    const media=window.matchMedia('(prefers-reduced-motion: reduce)');
    const finish=node=>{animations.get(node)?.cancel();animations.delete(node)};
    const visible=node=>{
      const rect=node.getBoundingClientRect(), bounds=scrollRoot?root.getBoundingClientRect():{top:0,bottom:window.innerHeight};
      return rect.bottom>bounds.top&&rect.top<bounds.bottom;
    };
    const observer=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
      let batch=0;
      entries.forEach(entry=>{
        if(!entry.isIntersecting)return;
        const node=entry.target;observer.unobserve(node);
        if(!root.contains(node))return;
        node.dataset.revealState='entered';
        if(media.matches||!node.animate||node.contains(document.activeElement))return;
        const animation=node.animate([{opacity:0,transform:'translateY(16px)'},{opacity:1,transform:'translateY(0)'}],{duration:420,delay:Math.min(batch++,3)*45,easing:'cubic-bezier(.22,1,.36,1)',fill:'backwards'});
        animations.set(node,animation);animation.onfinish=()=>finish(node);
      });
    },{root:scrollRoot?root:null,threshold:0,rootMargin:'0px 0px -18px 0px'}):null;
    const scan=node=>{
      const candidates=node.matches?.('[data-reveal]')?[node]:[];
      candidates.push(...(node.querySelectorAll?.('[data-reveal]')||[]));
      candidates.forEach(element=>{
        if(seen.has(element))return;
        seen.add(element);
        // A section owns its entrance; nested controls do not move twice.
        const parent=element.parentElement?.closest('[data-reveal]');
        if(parent&&root.contains(parent))return;
        if(media.matches||!observer||(scrollRoot&&visible(element))){element.dataset.revealState='entered';return;}
        element.dataset.revealState='observed';observer.observe(element);
      });
    };
    scan(root);
    const remove=node=>{
      const candidates=node.matches?.('[data-reveal]')?[node]:[];
      candidates.push(...(node.querySelectorAll?.('[data-reveal]')||[]));
      candidates.forEach(element=>{if(!root.contains(element)){observer?.unobserve(element);finish(element)}});
    };
    const mutations=new MutationObserver(records=>records.forEach(record=>{record.removedNodes.forEach(remove);record.addedNodes.forEach(scan)}));
    mutations.observe(root,{childList:true,subtree:true});
    const focus=event=>{
      const section=event.target.closest?.('[data-reveal]');
      if(section){observer?.unobserve(section);finish(section);section.dataset.revealState='entered';}
    };
    const preference=()=>{if(media.matches){observer?.disconnect();animations.forEach(animation=>animation.cancel());animations.clear();root.querySelectorAll('[data-reveal]').forEach(node=>{node.dataset.revealState='entered'})}};
    root.addEventListener('focusin',focus);media.addEventListener('change',preference);
    return()=>{observer?.disconnect();mutations.disconnect();animations.forEach(animation=>animation.cancel());root.removeEventListener('focusin',focus);media.removeEventListener('change',preference)};
  },[rootRef,scopeKey,scrollRoot]);
}

// Keep native details/summary keyboard semantics and allow reversing mid-flight.
export function AnimatedDetails({children,className='',open:initialOpen=false,...props}) {
  const [expanded,setExpanded]=useState(initialOpen);
  const [present,setPresent]=useState(initialOpen);
  const root=useRef(null), animation=useRef(null), fromHeight=useRef(null);
  const parts=React.Children.toArray(children);
  const summary=parts[0];
  useLayoutEffect(()=>{
    const node=root.current;
    if(!node)return;
    animation.current?.cancel();
    if(fromHeight.current==null)return;
    const start=fromHeight.current;fromHeight.current=null;
    if(reducedWorkflowMotion()||!node.animate){setPresent(expanded);return;}
    const style=getComputedStyle(node);
    const collapsed=node.querySelector('summary').getBoundingClientRect().height+parseFloat(style.paddingTop)+parseFloat(style.paddingBottom)+parseFloat(style.borderTopWidth)+parseFloat(style.borderBottomWidth);
    const end=expanded?node.getBoundingClientRect().height:collapsed;
    const active=node.animate([{height:`${start}px`},{height:`${end}px`}],{duration:expanded?280:200,easing:'cubic-bezier(.22,1,.36,1)'});
    animation.current=active;
    active.onfinish=()=>{animation.current=null;setPresent(expanded)};
  },[expanded]);
  useLayoutEffect(()=>()=>animation.current?.cancel(),[]);
  const toggle=event=>{
    summary.props.onClick?.(event);
    if(event.defaultPrevented)return;
    event.preventDefault();fromHeight.current=root.current.getBoundingClientRect().height;
    if(!expanded)setPresent(true);
    setExpanded(value=>!value);
  };
  return <details {...props} ref={root} open={present} className={`animated-details ${className}`}>
    {React.cloneElement(summary,{'aria-expanded':expanded,onClick:toggle})}
    <div className="animated-details-body" inert={!expanded} aria-hidden={!expanded}>{parts.slice(1)}</div>
  </details>;
}
