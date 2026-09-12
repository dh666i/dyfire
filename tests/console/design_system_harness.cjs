const {JSDOM}=require('jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
async function main(){
  const dom=new JSDOM(`<main><section class="panel"><input id="steady" value="keep"><button id="open" data-open-task-create>创建</button></section><div class="task-workbench"><section id="new-task" class="task-create-panel"><form data-task-create-form><select><option>账号</option></select><input id="draft"><button>提交</button></form></section></div><details class="studio-profile"><summary>账户</summary><div class="studio-profile-menu"><a href="/account-center">账户中心</a><form action="/logout"><button>退出登录</button></form></div></details><details class="row-menu"><summary>更多</summary><div class="row-menu-panel"><button>暂停</button></div></details></main>`,{url:'https://example.test/tasks',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;w.matchMedia=()=>({matches:false});w.performance.getEntriesByType=()=>[];
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
  w.eval(fs.readFileSync('spark_console/static/design_system.js','utf8'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));await tick();
  const input=w.document.querySelector('#steady');w.sparkDesignSystem.enhance();assert.equal(w.document.querySelectorAll('.ui-card-light').length,1);assert.equal(input,w.document.querySelector('#steady'));
  w.document.querySelector('#open').click();const drawer=w.document.querySelector('.ui-create-drawer');assert.equal(drawer.open,true);
  const draft=w.document.querySelector('#draft');draft.value='保留输入';draft.dispatchEvent(new w.Event('input',{bubbles:true}));
  drawer.querySelector('.ui-drawer-head button').click();assert.ok(drawer.querySelector('.ui-drawer-dirty'));assert.equal(drawer.open,true);
  drawer.querySelector('.ui-drawer-dirty button:last-child').click();assert.equal(drawer.open,false);assert.equal(draft.value,'保留输入');
  w.document.querySelector('#open').click();assert.equal(draft.value,'保留输入');
  w.eval(fs.readFileSync('spark_console/static/task_experience.js','utf8'));
  const review=w.sparkReview('确认',['安全文本 <script>']);assert.equal(w.document.querySelectorAll('dialog[open]').length,1);assert.ok(drawer.querySelector('section.task-review'));assert.equal(drawer.querySelector('section.task-review script'),null);
  drawer.querySelector('.task-review button').click();assert.equal(await review,false);assert.equal(drawer.querySelector('.task-create-panel').hidden,false);assert.equal(draft.value,'保留输入');
  w.document.dispatchEvent(new w.Event('task-created'));assert.equal(drawer.open,false);
  const menus=[...w.document.querySelectorAll('details')];menus[0].open=true;await tick();menus[1].open=true;await tick();assert.equal(menus[0].open,false);assert.equal(menus[1].open,true);
  menus[1].dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menus[1].open,false);
  dom.window.close();console.log('Shared cards, menu exclusivity, drawer draft retention and inline review passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
