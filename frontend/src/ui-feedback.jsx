import React, {useEffect, useId, useRef} from 'react';
import {Toaster, toast} from 'sonner';
import {StatusIcon} from './ui-icons.jsx';

// One host and one API across routes; operation ids update an existing notice.
export const notify = toast;

export function FeedbackHost() {
  return <Toaster position="bottom-center" className="feedback-host" closeButton
    visibleToasts={3} duration={4200} gap={10} offset={24}
    mobileOffset={{bottom:'calc(92px + env(safe-area-inset-bottom, 0px))',left:16,right:16}}
    containerAriaLabel="操作通知" customAriaLabel="操作结果"
    icons={{close:<StatusIcon kind="close"/>,success:<StatusIcon/>,error:<StatusIcon kind="error"/>,warning:<StatusIcon kind="warning"/>,info:<StatusIcon kind="info"/>,loading:<StatusIcon kind="loading" className="feedback-spinner"/>}}
    toastOptions={{unstyled:true,className:'app-toast',closeButtonAriaLabel:'关闭提示'}}/>;
}

// Compatibility bridge for existing page state. Notices survive navigation,
// while callbacks never update a component that has already unmounted.
export function Toast({message, onClose, tone='message'}) {
  const id=useId();
  const callback=useRef(onClose);
  const mounted=useRef(false);
  callback.current=onClose;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  useEffect(()=>{
    if(!message){toast.dismiss(id);return;}
    const done=()=>{if(mounted.current)callback.current?.()};
    (toast[tone]||toast.message)(message,{id,onDismiss:done,onAutoClose:done});
  },[message,tone,id]);
  return null;
}
