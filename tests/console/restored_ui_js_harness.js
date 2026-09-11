const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require(path.resolve(__dirname, "../../.artifacts/ui-check/node_modules/jsdom"));
const root = path.resolve(__dirname, "../..");

async function main() {
  const pages = JSON.parse(fs.readFileSync(0, "utf8"));
  for (const [route, html] of Object.entries(pages)) {
    const dom = new JSDOM(html, {url: `https://testserver${route}`, runScripts: "outside-only", pretendToBeVisual: true});
    const w = dom.window, d = w.document;
    w.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
    const directoryRequests = [];
    let healthRequests = 0;
    const nativeTimeout = w.setTimeout.bind(w);
    w.setTimeout = (callback, delay, ...args) => nativeTimeout(callback, delay === 1800 ? 1 : delay, ...args);
    w.fetch = async (url, options = {}) => {
      if (String(url).endsWith('/health-check')) {
        healthRequests++;
        return {ok:true, json:async()=>({message:'已安排登录复核，不会发送消息'})};
      }
      if (String(url) === '/accounts/health-state') {
        const health = {};
        for (const row of d.querySelectorAll('[data-health-account]')) health[row.dataset.healthAccount] = {
          state: healthRequests ? 'temporary' : 'valid', label:healthRequests ? '暂时异常' : '正常',
          reason:'测试检测依据', attempts:0, next_check_at:healthRequests ? '2026-09-09T12:00:00+00:00' : null
        };
        return {ok:true,json:async()=>health};
      }
      if (String(url).includes('/directory-jobs')) {
        directoryRequests.push({url, method: options.method || 'GET', body: options.body});
        const isPost = options.method === 'POST';
        return {ok: true, json: async () => ({id: 'job-1', status: isPost ? 'queued' : 'succeeded', limit: 20, items: [], has_more: true})};
      }
      return {ok: true, text: async () => '<input type="checkbox" data-bulk-item="tasks" name="task_ids" value="dynamic-task" form="admin-tasks-bulk" aria-label="选择测试任务">', json: async () => ({items: [
      {name: "Friend A", sec_uid: "stable-a", nickname: "小张", remark_name: "同学", unique_id: "zhang123", avatar_url: "https://p3.douyinpic.com/avatar.jpeg"},
      {name: "Friend B", sec_uid: "stable-b"}
    ]})}; };
    for (const script of [...d.querySelectorAll("script[src]")]) {
      const name = path.basename(new URL(script.src).pathname);
      assert(!["henng_ui.js", "workspace_ui.js"].includes(name), `redesign still active ${route}`);
      if (["mineradio_ui.js", "app_ui.js", "tasks.js", "password_toggle.js", "admin.js", "console_studio.js", "directory_ui.js", "account_health.js", "ui_select.js"].includes(name)) w.eval(fs.readFileSync(path.join(root, "spark_console/static", name), "utf8"));
      if (name === 'smoke_ui.js') {
        w.eval(fs.readFileSync(path.join(root,'spark_console/static/smoke_card.js'),'utf8').replace(/^export /gm,'')+'\nwindow.Card=Card;');
        w.eval(fs.readFileSync(path.join(root,'spark_console/static/smoke_ui.js'),'utf8').replace(/^import .*;\r?\n/gm,''));
      }
    }
    await new Promise(resolve => setTimeout(resolve, 10));
    const ids = [...d.querySelectorAll("[id]")].map(el => el.id);
    assert.equal(new Set(ids).size, ids.length, `duplicate IDs ${route}`);
    for (const input of d.querySelectorAll('input:not([type="hidden"]),select,textarea')) assert(input.labels.length || input.getAttribute("aria-label") || input.getAttribute("aria-labelledby"), `unlabelled control ${route}`);
    assert(!d.querySelector('link[href^="/static/henng.css"]'));
    assert(d.querySelector(`link[href^="/static/${route.startsWith('/admin/') ? 'mineradio_admin' : 'mineradio'}.css"]`));
    assert(!d.querySelector(".sp-workspace-header,[data-command-dialog]"));
    assert(d.querySelector('link[href^="/static/console_studio.css"]'));
    assert.equal(d.body.dataset.design, "studio");
    for (const mark of d.querySelectorAll('.brand-mark,.admin-brand-mark')) {
      assert(mark.querySelector('img[src^="/static/spark-flame.png"]'), `shared logo missing ${route}`);
      assert(!mark.querySelector('svg'), `retired logo still active ${route}`);
    }
    if (route === '/accounts') {
      const identity = d.querySelector('.account-identity');
      assert(identity.querySelector('.account-avatar'));
      assert(identity.querySelector('.account-summary'));
      assert(identity.nextElementSibling.matches('.account-controls'));
      const check = d.querySelector('[data-health-check]');
      check.click(); check.click();
      await new Promise(resolve=>setTimeout(resolve,20));
      assert.equal(healthRequests,1);
      assert(check.disabled);
      assert(d.querySelector('[data-health-label]').textContent.includes('暂时异常'));
      assert(d.querySelector('[data-health-feedback]').textContent.includes('不会发送消息'));
    }
    const themeToggle = d.querySelector('[data-theme-toggle]');
    themeToggle.click();
    assert.equal(d.documentElement.dataset.theme, 'dark');
    assert.equal(d.querySelector('[data-theme-color]').content, '#101112');
    themeToggle.click();
    assert.equal(d.documentElement.dataset.theme, 'light');
    assert.equal(d.querySelector('[data-theme-color]').content, '#f3f3f4');
    if (route === "/dashboard") {
      assert(d.querySelector(".studio-dashboard"));
      assert.equal(d.querySelectorAll('.studio-hour').length, 24);
      assert.equal(d.querySelectorAll('.studio-hour.has-tasks').length, 1);
      assert.equal(d.querySelector('.studio-hour.has-tasks .studio-hour-count').textContent, '13');
      assert.equal(d.querySelectorAll('.studio-metric').length, 4);
    }
    if (route === "/tasks") {
      assert.equal(d.querySelectorAll(".task-overview-row").length, 13);
      const create = d.querySelector('#new-task');
      assert.equal(create.tagName, 'SECTION');
      d.querySelector('[data-open-task-create]').click();
      assert(create.querySelector('[data-create-submit]'));
      assert(!d.querySelector('[data-task-filter-toolbar]').hidden);
      const search = d.querySelector('[data-task-search]');
      search.value = 'Friend 02';
      search.dispatchEvent(new w.Event('input'));
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length, 1);
      search.value = 'no such friend';
      search.dispatchEvent(new w.Event('input'));
      assert(!d.querySelector('[data-task-filter-empty]').hidden);
      d.querySelector('[data-task-filter-reset]').click();
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length, 12);
      d.querySelector('[data-next]').click();
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length, 1);
      d.querySelector('[data-prev]').click();
      d.querySelector('[data-task-filter="paused"]').click();
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length, 0);
      d.querySelector('[data-task-filter="enabled"]').click();
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length, 12);
      const size=d.querySelector('[data-size]');size.value='24';size.dispatchEvent(new w.Event('change'));
      assert.equal(d.querySelectorAll('.task-overview-row:not([hidden])').length,13);
      d.querySelector("#target-picker-trigger").click();
      assert(d.querySelector("#target-picker-dialog").open);
      assert.equal(d.querySelectorAll('.directory-actions button').length, 3);
      const friendSearch = d.querySelector('#target-search-input');
      for (const alias of ['小张', '同学', 'zhang123']) {
        friendSearch.value = alias;
        friendSearch.dispatchEvent(new w.Event('input'));
        assert.equal(d.querySelectorAll('.target-option').length, 1, 'alias search failed');
      }
      friendSearch.value = ''; friendSearch.dispatchEvent(new w.Event('input'));
      const avatar = d.querySelector(".target-option-avatar img");
      assert(avatar); avatar.dispatchEvent(new w.Event("error"));
      assert(!d.querySelector(".target-option-avatar img"));
      d.querySelector("[data-target-select-all]").click();
      d.querySelector("[data-target-confirm]").click();
      assert.equal(d.querySelectorAll('input[name="target_name"]').length, 2);
      d.querySelector('.directory-actions button').click();
      d.querySelector('.directory-actions button').click();
      await new Promise(resolve => setTimeout(resolve, 35));
      assert.equal(directoryRequests.filter(r => r.method === 'POST').length, 1, 'duplicate sync request');
      assert.equal(directoryRequests[0].body.get('limit'), '20');
      assert(d.querySelector('.directory-status').textContent.includes('同步完成'));
      assert.equal(d.querySelectorAll('input[name="target_name"]').length, 2, 'sync erased selections');
    }
    if (route === '/admin/tasks') {
      const group = d.querySelector('[data-task-group]');
      assert(group, 'account group missing');
      assert(!d.querySelector('[data-bulk-item="tasks"]'), 'group eagerly loaded');
      group.open = true; group.dispatchEvent(new w.Event('toggle'));
      await new Promise(resolve => setTimeout(resolve, 15));
      const checkbox = d.querySelector('[data-bulk-item="tasks"]');
      checkbox.checked = true; checkbox.dispatchEvent(new w.Event('change', {bubbles: true}));
      assert.equal(d.querySelector('[data-bulk-count]').textContent, '已选 1 项');
      assert(!d.querySelector('[data-bulk-submit]').disabled);
    }
    dom.window.close();
  }
  process.stdout.write(JSON.stringify({ok: true, pages: Object.keys(pages).length}));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
