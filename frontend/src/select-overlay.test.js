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
  const f=fixture(t);f.open();f.menu().children[1].dispatchEvent(new f.window.Event('pointerenter'));
  assert.equal(f.select.getAttribute('aria-activedescendant'),f.menu().children[1].id);
  f.key('Enter');assert.equal(f.select.value,'B');
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
test('creating a task, retrying or submitting without confirmation does not finish onboarding',()=>{
  for(const status of [undefined,'scheduled','retrying','failed','submitted_unconfirmed']){
    assert.equal(hasConfirmedExecution({tasks:[{}],runs:status?[{status}]:[]}),false);
  }
  assert.equal(hasConfirmedExecution({runs:[{status:'success'}]}),true);
  assert.equal(hasConfirmedExecution({onboarding:{has_successful_run:true},runs:[]}),true);
});
