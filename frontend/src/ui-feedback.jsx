import React, {useEffect, useId, useLayoutEffect, useRef} from 'react';
import {Toaster, toast, useSonner} from 'sonner';
import {StatusIcon} from './ui-icons.jsx';
import {createFeedbackTimers, feedbackOptions, FEEDBACK_LIFETIME} from './ui-feedback-state.js';

// One host and one API across routes; operation ids update an existing notice.
export const notify = Object.assign(
  (message, options) => toast(message, feedbackOptions(options)),
  Object.fromEntries(['message','success','error','warning','info','loading'].map(kind =>
    [kind, (message, options) => toast[kind](message, feedbackOptions(options))])),
  {dismiss: toast.dismiss, getToasts: toast.getToasts, getHistory: toast.getHistory},
);

export function FeedbackHost() {
  const region = useRef(null);
  const {toasts} = useSonner();
  const timers = useRef(null);
  const interaction = useRef({hover: false, focus: false, pressed: false, hidden: document.hidden});
  if (!timers.current) timers.current = createFeedbackTimers({
    now: () => performance.now(), schedule: (callback, duration) => window.setTimeout(callback, duration),
    cancel: timer => window.clearTimeout(timer),
    onExpire: notice => {
      const lifetime = notice[FEEDBACK_LIFETIME];
      if (lifetime?.closed) return;
      // Dismiss first: a callback that publishes a replacement with this id
      // can then cancel Sonner's pending dismissal rather than lose its notice.
      toast.dismiss(notice.id);
      notice.onAutoClose?.(notice);
    },
  });
  const updateInteraction = (kind, value) => {
    interaction.current[kind] = value;
    timers.current.setPaused(Object.values(interaction.current).some(Boolean));
  };
  useLayoutEffect(() => {
    // Re-read actual interaction after queue changes; a stationary pointer or
    // keyboard focus must keep pausing even when Sonner collapses its stack.
    interaction.current.hover = Boolean(region.current?.querySelector('[data-sonner-toast]:hover'));
    interaction.current.focus = Boolean(region.current?.contains(document.activeElement));
    timers.current.setPaused(Object.values(interaction.current).some(Boolean));
    timers.current.sync(toasts);
  }, [toasts]);
  useEffect(() => {
    const visibility = () => updateInteraction('hidden', document.hidden);
    const release = () => updateInteraction('pressed', false);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pointerup', release); window.removeEventListener('pointercancel', release);
      timers.current.clear();
    };
  }, []);
  return <div ref={region} className="feedback-region"
    onPointerEnter={event => { if (event.pointerType !== 'touch') updateInteraction('hover', true); }}
    onPointerLeave={() => updateInteraction('hover', false)}
    onPointerDownCapture={event => { if (event.button !== 2) updateInteraction('pressed', true); }}
    onFocusCapture={() => updateInteraction('focus', true)}
    onBlurCapture={event => updateInteraction('focus', event.currentTarget.contains(event.relatedTarget))}>
    <Toaster position="bottom-center" className="feedback-host" closeButton
    visibleToasts={3} duration={Infinity} gap={10} offset={24}
    mobileOffset={{bottom:'calc(92px + env(safe-area-inset-bottom, 0px))',left:16,right:16}}
    containerAriaLabel="操作通知" customAriaLabel="操作结果"
    icons={{close:<StatusIcon kind="close"/>,success:<StatusIcon/>,error:<StatusIcon kind="error"/>,warning:<StatusIcon kind="warning"/>,info:<StatusIcon kind="info"/>,loading:<StatusIcon kind="loading" className="feedback-spinner"/>}}
    toastOptions={{unstyled:true,className:'app-toast',closeButtonAriaLabel:'关闭提示'}}/>
  </div>;
}

// Compatibility bridge for existing page state. Notices survive navigation,
// while callbacks never update a component that has already unmounted.
export function Toast({message, onClose, tone='message'}) {
  const id=useId();
  const callback=useRef(onClose);
  const mounted=useRef(false);
  const active=useRef(false);
  callback.current=onClose;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  useEffect(()=>{
    if(!message){if(active.current)notify.dismiss(id);active.current=false;return;}
    active.current=true;
    const done=()=>{active.current=false;if(mounted.current)callback.current?.()};
    (notify[tone]||notify.message)(message,{id,onDismiss:done,onAutoClose:done});
  },[message,tone,id]);
  return null;
}
