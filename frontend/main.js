import './style.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/noto-sans-sc/400.css';
import '@fontsource/noto-sans-sc/500.css';
import { texture } from './noise.js';

const friends = [];
let tasks = [];
let nextId = 6, filter = 'all', term = '';
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const flame = `<svg viewBox="0 0 200 250" fill="none" aria-hidden="true"><path d="M107 7C125 60 70 74 89 113C106 111 119 91 123 73C159 107 188 143 178 185C169 228 133 245 98 244C52 245 19 215 20 174C20 137 44 110 57 81C56 114 63 127 75 135C45 78 109 64 107 7Z" fill="currentColor"/><path d="M105 138C119 171 141 173 139 199C135 222 112 231 94 224C76 218 70 201 79 185C84 201 97 201 101 193C106 182 94 168 105 138Z" fill="var(--bg)"/></svg>`;
document.querySelector('#app').innerHTML = `
<header><a class="brand" href="#workspace" aria-label="火花，返回任务">${flame}<span>火花<span class="brand-dot">®</span></span></a><nav aria-label="主导航"><a class="active" href="#workspace">续火任务</a><button data-info="账号管理">抖音账号</button><button data-info="执行记录">执行记录</button></nav><div class="header-end"><button id="theme" aria-label="切换到浅色模式">◐</button><span class="profile">迟</span></div></header>
<main><section class="hero" aria-labelledby="hero-title"><div class="hero-copy"><p class="eyebrow">KEEP THE CONNECTION. / 每天，都在。</p><h1 id="hero-title">不让默契<span>停在昨天<span class="period">.</span></span></h1><div class="hero-bottom"><p>把日常交给火花。<br>把时间留给在意的人。</p><a class="jump" href="#workspace">管理我的续火任务 <span>↘</span></a></div></div><div class="artifact" aria-hidden="true"><canvas id="grain"></canvas><div class="artifact-grid"></div><div class="emblem">${flame}</div><span class="artifact-index">FIG. 01 / CONNECTION</span><span class="artifact-bottom">STAY<br>IN TOUCH.</span><span class="artifact-cross">+</span></div></section>
<section class="status-strip" aria-label="任务概览"><div><span class="live-dot"></span><span id="account-status">正在连接</span></div><div>运行中 <b id="active-count"></b></div><div>今日已确认 <b id="confirmed-count"></b></div><span class="local-label">每天 / 北京时间</span></section>
<section id="workspace" class="workspace"><div class="task-area"><div class="section-heading"><div><p class="eyebrow">01 / YOUR ROUTINE</p><h2>续火任务<span id="total-count"></span></h2></div><label class="search"><span>⌕</span><input id="search" type="search" placeholder="搜索好友或备注" aria-label="搜索任务好友或备注"></label></div><div class="filters" role="group" aria-label="任务筛选"><button data-filter="all" class="selected" aria-pressed="true">全部任务</button><button data-filter="on" aria-pressed="false">运行中</button><button data-filter="off" aria-pressed="false">已暂停</button><span>每日 / 北京时间</span></div><div class="table-heading"><span>目标好友</span><span>发送时间</span><span>今日状态</span><span>操作</span></div><div id="task-list"></div><div class="list-footer"><span id="results"></span><span>消息提交后，等待抖音确认</span></div></div>
<aside class="composer" aria-labelledby="create-heading"><div class="compose-title"><div><p class="eyebrow">02 / NEW CONNECTION</p><h2 id="create-heading">新建续火任务</h2></div><span>↗</span></div><form id="create-form"><label class="field-label" for="account">发送账号</label><select id="account"><option value="">请选择账号</option></select><div class="field-top"><label class="field-label">目标好友</label><span id="selected-count">已选 0 人</span></div><button type="button" class="friend-trigger" id="choose-friends"><span id="friend-summary">选择一个或多个好友</span><span>＋</span></button><div class="two-fields"><div><label class="field-label" for="time">每日发送时间</label><input type="time" id="time" value="09:30" required></div><div><label class="field-label" for="kind">消息类型</label><select id="kind"><option>续火花表情</option><option>文字消息</option></select></div></div><p class="schedule-hint" id="schedule-hint">多位好友会自动错开 5 分钟，并优先安排到全站较空闲的时段。</p><div class="schedule-suggestions" id="schedule-suggestions" hidden></div><label class="field-label" for="message" id="message-label" hidden>发送内容</label><textarea id="message" hidden maxlength="300" placeholder="写一句日常问候"></textarea><div class="message-preview"><span class="tiny-flame">${flame}</span><div><b id="preview-title">续火花表情</b><p id="preview-copy">每天定时，给选中的好友发送续火花表情。</p></div></div><p class="feedback" id="form-feedback" role="alert"></p><button type="submit" class="submit">创建续火任务 <span>↗</span></button><p class="form-note">按北京时间每天执行。批量创建会错开 5 分钟，避免同一时间集中发送。</p></form></aside></section>
<footer><span>火花 / KEEP IN TOUCH.</span><span>让关系持续，让操作简单。</span></footer></main>
<dialog id="friends-dialog"><div class="dialog-top"><div><p class="eyebrow">SELECT CONNECTIONS</p><h2>选择目标好友</h2></div><button type="button" data-close="friends-dialog" aria-label="关闭好友选择">✕</button></div><label class="search dialog-search"><span>⌕</span><input type="search" id="friend-search" aria-label="搜索好友" placeholder="输入昵称或备注"></label><div class="selection-tools"><button id="select-visible">全选当前结果</button><button id="clear-selection">清空选择</button></div><div id="friend-list">${friends.map((n,i)=>`<label class="friend-option" data-name="${n.toLowerCase()}"><input type="checkbox" value="${i}" name="friend"><span class="avatar a${i%4}">${n[0]}</span><span>${n}<small>抖音好友</small></span><span class="checkmark">✓</span></label>`).join('')}</div><p id="friend-empty" hidden>没有找到匹配的好友。</p><div class="dialog-bottom"><span id="dialog-selected">已选 0 人</span><button class="submit" data-close="friends-dialog">完成选择 ↗</button></div></dialog>
<dialog id="info-dialog"><div class="dialog-top"><h2 id="info-title"></h2><button data-close="info-dialog" aria-label="关闭">✕</button></div><div id="info-content"></div><p class="form-note"></p></dialog>`;
try { texture(document.querySelector('#grain')); } catch {}
const $ = s => document.querySelector(s);
const selected = () => [...document.querySelectorAll('[name=friend]:checked')].map(c => friends[Number(c.value)]);
function render() {
  const shown = tasks.filter(t => (!term || t.name.toLowerCase().includes(term)) && (filter === 'all' || (filter === 'on' ? t.enabled : !t.enabled)));
  $('#task-list').innerHTML = shown.length ? shown.map(t=>`<article class="task-row"><div class="person"><span class="avatar a${friends.indexOf(t.name)%4}" aria-hidden="true"></span><div><h3>${esc(t.name)}</h3><p>${esc(t.kind)}</p></div></div><div class="task-time">${t.time}<small>每天</small></div><span class="state ${!t.enabled?'paused':t.confirmed?'confirmed':'waiting'}"><i></i>${!t.enabled?'已暂停':t.confirmed?'发送已确认':t.status==='submitted_unconfirmed'?'已提交，待核实':t.status==='failed'?'执行失败':t.status==='running'?'执行中':t.status==='retrying'?'等待恢复重试':'等待执行'}</span><button class="toggle" data-toggle="${t.id}" aria-label="${t.enabled?'暂停':'恢复'} ${esc(t.name)} 的任务">${t.enabled?'暂停':'恢复'}</button></article>`).join('') : '<div class="empty">没有匹配的任务。<button id="reset">查看全部任务 ↗</button></div>';
  $('#active-count').textContent = String(tasks.filter(t=>t.enabled).length).padStart(2,'0');
  $('#confirmed-count').textContent = String(tasks.filter(t=>t.confirmed).length).padStart(2,'0');
  $('#total-count').textContent = `(${String(tasks.length).padStart(2,'0')})`;
  $('#results').textContent = `${shown.length} / ${tasks.length} 个任务`;
  document.dispatchEvent(new Event('studio:render-tasks'));
}
function syncSelection() { const list = selected(); $('#selected-count').textContent = $('#dialog-selected').textContent = `已选 ${list.length} 人`; $('#friend-summary').textContent = list.length ? `${list.slice(0,2).join('、')}${list.length>2?` 等 ${list.length} 人`:''}` : '选择一个或多个好友'; }
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3000); }
$('#choose-friends').onclick = () => $('#friends-dialog').showModal();
document.addEventListener('click', e => {
  const close = e.target.closest('[data-close]'); if(close) $('#'+close.dataset.close).close();
  const f = e.target.closest('[data-filter]'); if(f) {filter=f.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>{b.classList.toggle('selected',b===f);b.setAttribute('aria-pressed',String(b===f));});render();}
  if(e.target.id==='reset') {term='';$('#search').value='';$('[data-filter="all"]').click();}
});
$('#search').oninput=e=>{term=e.target.value.trim().toLowerCase();render();};
$('#friend-search').oninput=e=>{let count=0;document.querySelectorAll('.friend-option').forEach(el=>{el.hidden=!el.dataset.name.includes(e.target.value.trim().toLowerCase());if(!el.hidden)count++;});$('#friend-empty').hidden=!!count;};
document.querySelectorAll('[name=friend]').forEach(c=>c.onchange=syncSelection);
$('#select-visible').onclick=()=>{document.querySelectorAll('.friend-option:not([hidden]) input:not(:disabled)').forEach(c=>c.checked=true);syncSelection();};
$('#clear-selection').onclick=()=>{document.querySelectorAll('[name=friend]').forEach(c=>c.checked=false);syncSelection();};
$('#kind').onchange=()=>{const text=$('#kind').value==='文字消息';$('#message').hidden=$('#message-label').hidden=!text;$('#message').required=text;$('#preview-title').textContent=text?'自定义文字消息':'续火花表情';$('#preview-copy').textContent=text?'将按设定时间发送你填写的内容。':'每天定时，给选中的好友发送续火花表情。';};

let light=false;try{light=localStorage.getItem('studio-theme')==='light';}catch{}
function theme(){document.documentElement.dataset.theme=light?'light':'dark';$('#theme').setAttribute('aria-label',`切换到${light?'深':'浅'}色模式`);}
$('#theme').onclick=()=>{light=!light;theme();try{localStorage.setItem('studio-theme',light?'light':'dark');}catch{}};
theme();render();

export const studio = { get tasks(){ return tasks; }, set tasks(value){ tasks=value; }, friends, render, toast, esc, flame, syncSelection };
