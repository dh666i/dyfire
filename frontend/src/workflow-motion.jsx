import React, {useLayoutEffect, useRef, useState} from 'react';
import './workflow-motion.css';

export function reducedWorkflowMotion() {
  return typeof window === 'undefined' || Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

function motionOptions(node, token, fallback) {
  const styles = node.ownerDocument.defaultView?.getComputedStyle(node);
  const raw = styles?.getPropertyValue(token).trim();
  const value = Number.parseFloat(raw);
  const duration = Number.isFinite(value) ? value * (raw.endsWith('ms') ? 1 : 1000) : fallback;
  return {duration, easing:styles?.getPropertyValue('--motion-ease-standard').trim() || styles?.getPropertyValue('--ease-standard').trim() || 'cubic-bezier(.2,0,0,1)', fill:'both'};
}

function keyedElements(container) {
  return Array.from(container.children).filter(node => node.hasAttribute('data-motion-key') && !node.hasAttribute('data-motion-ghost'));
}

// Capture positions before React changes the DOM.  Translation-only FLIP keeps
// the remaining rows visually in place while the list adopts its final layout.
// Removed rows are inert visual copies, so old controls never remain usable.
export class MotionList extends React.Component {
  root = React.createRef();
  animations = new Map();
  ghosts = new Map();

  getSnapshotBeforeUpdate() {
    const root = this.root.current;
    if (!root || reducedWorkflowMotion()) return null;
    const rootRect = root.getBoundingClientRect();
    const nextKeys = new Set();
    React.Children.forEach(this.props.children, child => {
      if (React.isValidElement(child) && child.key != null) nextKeys.add(String(child.key));
    });
    return keyedElements(root).map(node => {
      const rect = node.getBoundingClientRect();
      const key = node.dataset.motionKey;
      return {
        key,
        rect,
        state:node.dataset.motionState,
        clone:nextKeys.has(key) ? null : node.cloneNode(true),
        left:rect.left-rootRect.left+root.scrollLeft-root.clientLeft,
        top:rect.top-rootRect.top+root.scrollTop-root.clientTop,
      };
    });
  }

  animate = (node, frames, token='--motion-medium', fallback=180) => {
    this.animations.get(node)?.cancel();
    if (!node.animate || reducedWorkflowMotion()) return;
    const animation = node.animate(frames, motionOptions(node, token, fallback));
    this.animations.set(node, animation);
    const finish = () => {
      if (this.animations.get(node) !== animation) return;
      this.animations.delete(node);
      animation.cancel();
    };
    animation.onfinish = finish;
    animation.oncancel = () => {
      if (this.animations.get(node) === animation) this.animations.delete(node);
    };
    return animation;
  };

  componentDidUpdate(_previousProps, _previousState, snapshot) {
    const root = this.root.current;
    if (!root) return;
    // Read the interrupted visual position from the snapshot before cancelling
    // previous motion, then measure the final layout with no transform applied.
    this.animations.forEach(animation => animation.cancel());
    this.animations.clear();
    if (!snapshot || reducedWorkflowMotion()) {
      this.clearGhosts();
      return;
    }
    const previous = new Map(snapshot.map(row => [row.key, row]));
    const current = keyedElements(root);
    const currentKeys = new Set(current.map(node => node.dataset.motionKey));
    current.forEach(node => {
      const key = node.dataset.motionKey;
      this.removeGhost(key);
      const old = previous.get(key);
      if (!old) {
        this.animate(node, [{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'none'}]);
        return;
      }
      const rect = node.getBoundingClientRect();
      const x = old.rect.left-rect.left;
      const y = old.rect.top-rect.top;
      if (Math.abs(x)>0.5 || Math.abs(y)>0.5) this.animate(node, [{transform:`translate(${x}px,${y}px)`},{transform:'none'}]);
      if (old.state !== node.dataset.motionState) {
        const target = node.querySelector('[data-motion-feedback], .badge') || node;
        this.animate(target, [{opacity:.5,transform:'translateY(2px)'},{opacity:1,transform:'none'}], '--motion-short', 150);
      }
    });
    snapshot.forEach(old => {
      if (currentKeys.has(old.key) || !old.clone) return;
      this.removeGhost(old.key);
      const ghost = old.clone;
      ghost.setAttribute('data-motion-ghost','');
      ghost.setAttribute('aria-hidden','true');
      ghost.inert = true;
      ghost.removeAttribute('data-motion-key');
      ghost.removeAttribute('id');
      ghost.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
      ghost.querySelectorAll('input,button,select,textarea').forEach(node => { node.disabled=true; });
      Object.assign(ghost.style, {position:'fixed',left:`${old.rect.left}px`,top:`${old.rect.top}px`,width:`${old.rect.width}px`,height:`${old.rect.height}px`,margin:'0',pointerEvents:'none'});
      root.ownerDocument.body.appendChild(ghost);
      const animation = this.animate(ghost, [{opacity:1,transform:'none'},{opacity:0,transform:'translateX(-6px)'}], '--motion-quick', 150);
      if (!animation) { ghost.remove(); return; }
      const timer = window.setTimeout(() => this.removeGhost(old.key), motionOptions(ghost, '--motion-quick', 150).duration+40);
      this.ghosts.set(old.key,{node:ghost,timer});
      animation.onfinish = () => this.removeGhost(old.key);
    });
  }

  removeGhost = key => {
    const ghost = this.ghosts.get(key);
    if (!ghost) return;
    window.clearTimeout(ghost.timer);
    this.animations.get(ghost.node)?.cancel();
    this.animations.delete(ghost.node);
    ghost.node.remove();
    this.ghosts.delete(key);
  };

  clearGhosts = () => { Array.from(this.ghosts.keys()).forEach(this.removeGhost); };

  componentWillUnmount() {
    this.clearGhosts();
    this.animations.forEach(animation => animation.cancel());
    this.animations.clear();
  }

  render() {
    const {children,className='',...props} = this.props;
    return <div {...props} ref={this.root} className={`workflow-motion-list ${className}`.trim()}>{React.Children.map(children, child => React.isValidElement(child) && child.key != null ? React.cloneElement(child, {'data-motion-key':String(child.key)}) : child)}</div>;
  }
}

// Closing content remains mounted through its fade but becomes inaccessible
// immediately.  Reopening cancels that fade before it can hide the content.
export function MotionDisclosure({open,children,className='',...props}) {
  const [mounted,setMounted] = useState(Boolean(open));
  const ref = useRef(null);
  const animation = useRef(null);
  const generation = useRef(0);
  useLayoutEffect(() => {
    const sequence = ++generation.current;
    const node = ref.current;
    const currentHeight=animation.current&&node?node.getBoundingClientRect().height:null;
    const opacity=animation.current&&node?getComputedStyle(node).opacity:null;
    animation.current?.cancel();animation.current=null;
    if(open&&!mounted){setMounted(true);return;}
    if(!node||!mounted)return;
    if(!node.animate||reducedWorkflowMotion()){node.style.overflow='';if(!open)setMounted(false);return;}
    const naturalHeight=node.getBoundingClientRect().height;
    const active=node.animate(open
      ? [{height:`${currentHeight??0}px`,opacity:opacity??0},{height:`${naturalHeight}px`,opacity:1}]
      : [{height:`${currentHeight??naturalHeight}px`,opacity:opacity??1},{height:'0px',opacity:0}],
      {...motionOptions(node,'--motion-medium',260),duration:open?280:200});
    node.style.overflow='clip';animation.current=active;
    active.onfinish=()=>{
      if(generation.current!==sequence)return;
      animation.current=null;
      if(!open)setMounted(false);
      active.cancel();node.style.overflow='';
    };
    // The next layout effect reads the interrupted height before cancelling.
  },[open,mounted]);
  useLayoutEffect(()=>()=>{generation.current++;animation.current?.cancel()},[]);
  return <div {...props} ref={ref} className="workflow-disclosure" hidden={!mounted} aria-hidden={!open} inert={!open}>{mounted?<div className={className}>{children}</div>:null}</div>;
}
