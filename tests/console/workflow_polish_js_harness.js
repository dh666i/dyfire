const assert=require('node:assert/strict'),fs=require('fs');
const {JSDOM}=require('../../.artifacts/ui-check/node_modules/jsdom');
(async()=>{
const dom=new JSDOM(`<details class="row-menu"><summary>更多</summary><button>暂停</button></details><button id="outside">外部</button><div class="mail-workspace"><div class="admin-table-wrap"><table class="admin-table"><thead><tr><th>时间</th><th>邮箱</th><th>通知</th><th>结果</th><th>说明</th></tr></thead><tbody><tr><td>今天</td><td>a@example.com</td><td>恢复</td><td>服务器已接受</td><td>—</td></tr><tr><td>昨天</td><td>b@example.com</td><td>异常</td><td>失败</td><td>认证失败</td></tr></tbody></table></div></div><script id="mail-template-data" type="application/json">{"a":{"subject":"A","body":"原始A"},"b":{"subject":"B","body":"原始B"}}</script><form id="template-form"><select name="event"><option>a</option><option>b</option></select><input name="subject"><textarea name="body"></textarea><p data-mail-feedback></p></form>`,{url:'https://example.test/admin/settings/mail',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document;
w.eval(fs.readFileSync('spark_console/static/workflow_polish.js','utf8'));
assert.equal(d.querySelectorAll('.mobile-record').length,2);
const search=d.querySelector('.record-tools input');search.value='b@example.com';search.dispatchEvent(new w.Event('input'));
assert.equal(d.querySelectorAll('.mobile-record:not([hidden])').length,1);assert.equal(d.querySelectorAll('tbody tr:not([hidden])').length,1);
d.querySelector('.record-tools button').click();assert.equal(d.querySelectorAll('.mobile-record:not([hidden])').length,2);
const menu=d.querySelector('.row-menu');menu.open=true;d.querySelector('#outside').focus();assert.equal(menu.open,false);
menu.open=true;menu.querySelector('button').focus();d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menu.open,false);assert.equal(d.activeElement,menu.querySelector('summary'));
w.eval(fs.readFileSync('spark_console/static/mail_ui.js','utf8'));
const form=d.querySelector('form');form.elements.body.value='未保存A';form.elements.event.value='b';form.elements.event.dispatchEvent(new w.Event('change'));
assert.equal(form.elements.body.value,'原始B');form.elements.event.value='a';form.elements.event.dispatchEvent(new w.Event('change'));assert.equal(form.elements.body.value,'未保存A');
dom.window.close();console.log('PASS mobile and desktop filtering, menu outside/keyboard focus, unsaved template switching');
})().catch(e=>{console.error(e);process.exitCode=1;});
