import React, {useEffect, useId, useRef, useState} from 'react';
import {Select} from '@base-ui/react/select';
import NumberFlow from '@number-flow/react';
import {notify} from './ui-feedback.jsx';
import {StatusIcon} from './ui-icons.jsx';
export {StatusIcon} from './ui-icons.jsx';

export function Button({children,variant='',type='button',className='',busy=false,busyLabel,onClick,disabled, ...props}) {
  const [pending,setPending]=useState(false);
  const lock=useRef(false);
  const alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
  const loading=busy||pending;
  const click=event=>{
    if(lock.current||loading||disabled){event.preventDefault();return;}
    let result;
    try {result=onClick?.(event)} catch(error){notify.error(error.message||'操作未完成，请重试');return;}
    if(!result||typeof result.then!=='function')return;
    lock.current=true;setPending(true);
    Promise.resolve(result).catch(error=>notify.error(error.message||'操作未完成，请重试')).finally(()=>{
      lock.current=false;if(alive.current)setPending(false);
    });
  };
  return <button {...props} type={type} className={`button quality-button ${variant} ${className}`.trim()}
    disabled={disabled||loading} aria-busy={loading||undefined} onClick={click}>
    <span className="button-label" aria-hidden={loading||undefined}>{children}</span>
    {loading&&<span className="button-progress" aria-label={busyLabel||'处理中'}><StatusIcon kind="loading"/>{busyLabel}</span>}
  </button>;
}

export function LiveNumber({value,...props}) {
  if(typeof value!=='number'||!Number.isFinite(value))return value;
  return <NumberFlow value={value} locales="zh-CN" format={{useGrouping:true,maximumFractionDigits:2}}
    transformTiming={{duration:320,easing:'cubic-bezier(.22,1,.36,1)'}} {...props}/>;
}

export function SiteSelect({value,onValueChange,items,label,name,disabled=false,className=''}) {
  const id=useId();
  return <Select.Root value={value} onValueChange={onValueChange} items={items} name={name} disabled={disabled}>
    <Select.Trigger id={id} aria-label={label} className={`input site-select ${className}`}>
      <Select.Value/><Select.Icon><StatusIcon kind="chevron"/></Select.Icon>
    </Select.Trigger>
    <Select.Portal><Select.Positioner className="site-select-positioner" sideOffset={7} alignItemWithTrigger={false}>
      <Select.Popup className="site-select-popup"><Select.List>
        {items.map(item=><Select.Item key={item.value} value={item.value} disabled={item.disabled} className="site-select-option">
          <Select.ItemText>{item.label}</Select.ItemText><Select.ItemIndicator><StatusIcon/></Select.ItemIndicator>
        </Select.Item>)}
      </Select.List></Select.Popup>
    </Select.Positioner></Select.Portal>
  </Select.Root>;
}
