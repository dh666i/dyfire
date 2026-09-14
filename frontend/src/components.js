export function createUI(esc) {
const field=(label,name,value='',type='text',extra='')=>`<label class="edit-field">${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
const switcher=(label,name,checked,desc='')=>`<label class="switch-row"><span><b>${label}</b>${desc?`<small>${desc}</small>`:''}</span><input type="checkbox" name="${name}" ${checked?'checked':''}></label>`;
const pill=(text,tone='')=>`<span class="badge ${tone}"><i></i>${text}</span>`;
const button=(action,label,id='',cls='')=>`<button class="small-action ${cls}" data-action="${action}" data-id="${esc(id)}">${label}</button>`;
const empty=(title,description='')=>`<div class="empty-state"><h3>${title}</h3><p>${description}</p></div>`;
function pageHead(meta,action=''){return `<div class="workspace-heading" data-workspace-heading><div><p class="eyebrow">${meta[0]}</p><h1>${meta[1]}<span class="workspace-period" aria-hidden="true">.</span></h1><p>${meta[2]}</p></div>${action}</div>`;}
const section=(id,title,body,action='')=>`<section class="editorial-section workspace-section"><div class="section-label"><div><p class="eyebrow">${id}</p><h2>${title}</h2></div>${action}</div>${body}</section>`;
function footer(){return '<footer><span>火花 / KEEP IN TOUCH.</span><span>让关系持续，让操作简单。</span><a href="/studio">返回续火任务 ↗</a></footer>';}

return {field,switcher,pill,button,empty,pageHead,section,footer};
}
