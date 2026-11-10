import {useCallback} from 'react';
import {flushSync} from 'react-dom';
import {useNavigate} from 'react-router-dom';

let currentTransition;
let generation=0;
// Shared identities are optional enhancement. Routing works immediately on
// unsupported browsers and never depends on the animation finishing.
export function useAccountNavigation() {
  const navigate=useNavigate();
  return useCallback((account,source)=>{
    const sequence=++generation;
    const target=`/studio?account=${encodeURIComponent(account.id)}`;
    currentTransition?.skipTransition();
    if(!document.startViewTransition||window.matchMedia('(prefers-reduced-motion: reduce)').matches){navigate(target);return;}
    const avatar=source?.querySelector('[data-account-avatar],.avatar');
    const title=source?.querySelector('[data-account-name],h2,b');
    const mark=(node,name)=>{if(node){node.dataset.contextTransition=String(sequence);node.style.viewTransitionName=name}};
    const reset=()=>{for(const node of [avatar,title]){if(node?.dataset.contextTransition===String(sequence)){node.style.removeProperty('view-transition-name');delete node.dataset.contextTransition}}};
    mark(avatar,'account-avatar');mark(title,'account-title');
    let navigated=false;
    try {
      currentTransition=document.startViewTransition(()=>{reset();if(sequence!==generation)return;flushSync(()=>{navigate(target);navigated=true})});
      currentTransition.ready.catch(()=>{});
      currentTransition.finished.catch(()=>{}).finally(reset);
    }catch {reset();if(!navigated&&sequence===generation)navigate(target)}
  },[navigate]);
}
