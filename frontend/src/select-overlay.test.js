import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {installSelectOverlay} from './select-overlay.js';
import {hasConfirmedExecution} from './onboarding-state.js';

function fixture(t) {
  const dom=new JSDOM('<button id="outside">outside</button><select aria-label="账号"><option>A</option><option>B</option><option disabled>C</option><option>D</option></select>');
  const {window}=dom,select=window.document.querySelector('select');
  const overlay=installSelectOverlay(window),errors=[],changes=[];
  window.addEventListener('error',event=>{errors.push(event.message);event.preventDefault()});
  select.addEventListener('change',()=>changes.push(select.value));
  const key=value=>select.dispatchEvent(new window.KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true}));
  const pointer=element=>element.dispatchEvent(new window.Event('pointerdown',{bubbles:true,cancelable:true}));
  const open=()=>pointer(select);
  t.after(()=>{assert.deepEqual(errors,[]);overlay.destroy();dom.window.close()});
  return {window,select,errors,changes,key,pointer,open,menu:()=>window.document.querySelector('.fire-select-menu')};
}

test('a pointer choice publishes the intended value and restores focus without throwing',t=>{
  const f=fixture(t);f.open();f.menu().children[1].click();
  assert.equal(f.select.value,'B');assert.deepEqual(f.changes,['B']);assert.equal(f.menu(),null);
  assert.equal(f.window.document.activeElement,f.select);
});
test('arrows highlight without committing; Enter commits that same highlight',t=>{
  const f=fixture(t);f.open();f.key('ArrowDown');
  assert.equal(f.select.value,'A');assert.deepEqual(f.changes,[]);
  assert.equal(f.menu().children[1].classList.contains('is-active'),true);
  f.key('Enter');assert.equal(f.select.value,'B');assert.deepEqual(f.changes,['B']);
});
test('keyboard navigation skips disabled options and Home/End select enabled endpoints',t=>{
  const f=fixture(t);f.open();f.key('ArrowDown');f.key('ArrowDown');f.key('Enter');assert.equal(f.select.value,'D');
  f.open();f.key('Home');f.key('Enter');assert.equal(f.select.value,'A');
  f.open();f.key('End');f.key(' ');assert.equal(f.select.value,'D');
});
test('pointer hover then Enter never advances to another option',t=>{
  const f=fixture(t);f.open();f.menu().children[1].dispatchEvent(new f.window.Event('pointermove'));
  assert.equal(f.select.getAttribute('aria-activedescendant'),f.menu().children[1].id);
  f.key('Enter');assert.equal(f.select.value,'B');
});
test('stationary pointer enter and touch motion cannot overwrite a keyboard highlight',t=>{
  const f=fixture(t);f.open();f.key('ArrowDown');
  const option=f.menu().children[0];
  option.dispatchEvent(new f.window.Event('pointerenter'));
  const touch=new f.window.Event('pointermove');Object.defineProperty(touch,'pointerType',{value:'touch'});
  option.dispatchEvent(touch);
  assert.equal(f.select.getAttribute('aria-activedescendant'),f.menu().children[1].id);
  f.key('Enter');assert.equal(f.select.value,'B');assert.deepEqual(f.changes,['B']);
});
test('scrolling inside menu keeps it open; scrolling outside closes it',t=>{
  const f=fixture(t);f.open();const menu=f.menu();menu.dispatchEvent(new f.window.Event('scroll'));
  assert.equal(f.menu(),menu);f.window.dispatchEvent(new f.window.Event('scroll'));assert.equal(f.menu(),null);
});
test('Escape, Tab and clicking the same select close the popup without changing the value',t=>{
  const f=fixture(t);f.open();f.key('ArrowDown');f.key('Escape');assert.equal(f.menu(),null);
  f.open();f.key('Tab');assert.equal(f.menu(),null);f.open();f.pointer(f.select);assert.equal(f.menu(),null);
  assert.deepEqual(f.changes,[]);assert.equal(f.select.hasAttribute('aria-controls'),false);
});
test('disabled and multi-select controls retain native behavior',t=>{
  const f=fixture(t);f.select.disabled=true;f.open();assert.equal(f.menu(),null);
  f.select.disabled=false;f.select.multiple=true;f.open();assert.equal(f.menu(),null);
});
test('an iOS touch gesture blocks every native picker action and toggles the custom list only once',t=>{
  const f=fixture(t);
  const dispatch=type=>{
    const event=new f.window.Event(type,{bubbles:true,cancelable:true});
    f.select.dispatchEvent(event);assert.equal(event.defaultPrevented,true,type);
  };
  for(const type of ['pointerdown','touchstart','touchend','mousedown','click'])dispatch(type);
  assert.equal(f.menu()?.getAttribute('role'),'listbox');
  assert.equal(f.select.getAttribute('aria-expanded'),'true');
  for(const type of ['pointerdown','touchstart','touchend','mousedown','click'])dispatch(type);
  assert.equal(f.menu(),null);assert.deepEqual(f.changes,[]);
});
test('click-only accessible activation opens and closes the custom list without native activation',t=>{
  const f=fixture(t);
  for(const expanded of [true,false]){
    const click=new f.window.MouseEvent('click',{bubbles:true,cancelable:true,detail:0});
    f.select.dispatchEvent(click);
    assert.equal(click.defaultPrevented,true);
    assert.equal(Boolean(f.menu()),expanded);
  }
});
test('touch cancellation never takes over disabled, multiple or visible list controls',t=>{
  const f=fixture(t);
  for(const properties of [{disabled:true},{disabled:false,multiple:true},{multiple:false,size:4}]){
    Object.assign(f.select,properties);
    for(const type of ['pointerdown','touchstart','touchend','mousedown','click']){
      const event=new f.window.Event(type,{bubbles:true,cancelable:true});
      f.select.dispatchEvent(event);assert.equal(event.defaultPrevented,false,type);
    }
    assert.equal(f.menu(),null);
  }
});
test('older touch-only browsers open a custom list and choose once',t=>{
  const f=fixture(t);
  const start=new f.window.Event('touchstart',{bubbles:true,cancelable:true});
  f.select.dispatchEvent(start);assert.equal(start.defaultPrevented,true);
  f.select.dispatchEvent(new f.window.Event('touchend',{bubbles:true,cancelable:true}));
  f.select.dispatchEvent(new f.window.Event('touchstart',{bubbles:true,cancelable:true}));
  assert.equal(f.menu(),null);
  f.select.dispatchEvent(new f.window.Event('touchstart',{bubbles:true,cancelable:true}));
  f.menu().children[1].click();
  assert.equal(f.select.value,'B');assert.deepEqual(f.changes,['B']);
});
test('creating a task, retrying or submitting without confirmation does not finish onboarding',()=>{
  for(const status of [undefined,'scheduled','retrying','failed','submitted_unconfirmed']){
    assert.equal(hasConfirmedExecution({tasks:[{}],runs:status?[{status}]:[]}),false);
  }
  assert.equal(hasConfirmedExecution({runs:[{status:'success'}]}),true);
  assert.equal(hasConfirmedExecution({onboarding:{has_successful_run:true},runs:[]}),true);
});
