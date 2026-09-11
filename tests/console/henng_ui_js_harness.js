const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require(path.resolve(__dirname, "../../.artifacts/ui-check/node_modules/jsdom"));
const root = path.resolve(__dirname, "../..");
const {taskPage, scheduleHours} = require(path.join(root, "spark_console/static/henng_ui.js"));
const items = Array.from({length: 31}, (_, i) => ({search: `Friend ${i}`, state: i % 2 ? "paused" : "enabled"}));
assert.equal(taskPage(items).items.length, 12);
assert.equal(taskPage(items, "", "", 3).items.length, 7);
assert.equal(taskPage(items, "", "", 99).page, 3);
assert.equal(taskPage(items, "", "paused").total, 15);
assert.equal(taskPage(items, "FRIEND 30").total, 1);
assert.equal(taskPage(items, "missing").total, 0);
assert.equal(taskPage(items, "", "", -2).page, 1);
const bins = scheduleHours([{time: "00:00", enabled: true}, {time: "23:59", enabled: false}, {time: "00:30", enabled: false}, {time: "24:00", enabled: true}, {time: "09:60", enabled: true}, {time: "x", enabled: true}]);
assert.equal(bins.length, 24);
assert.equal(bins[0].tasks.length, 2);
assert.equal(bins[0].enabled, 1);
assert.equal(bins[23].tasks.length, 1);
assert.equal(bins[23].enabled, 0);
assert.equal(bins.flatMap(bin => bin.tasks).length, 3);

async function main() {
  const pages = JSON.parse(fs.readFileSync(0, "utf8"));
  for (const [route, html] of Object.entries(pages)) {
    const dom = new JSDOM(html, {url: `https://testserver${route}`, runScripts: "outside-only", pretendToBeVisual: true});
    const {window: w} = dom;
    const d = w.document;
    w.matchMedia = () => ({matches: false, addEventListener() {}});
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
    w.fetch = async (url) => {
      const pathname = new URL(url, w.location.href).pathname;
      if (pathname.endsWith("/conversations")) return {ok: true, redirected: false, json: async () => ({items: [
        {name: "Friend A", sec_uid: "stable-a", avatar_url: "https://p3.douyinpic.com/avatar.jpeg"},
        {name: "Friend B", sec_uid: "stable-b"}
      ]})};
      return {ok: true, redirected: false, url: pathname, text: async () => pages[pathname]};
    };
    for (const script of [...d.querySelectorAll("script[src]")]) {
      const name = path.basename(new URL(script.src).pathname);
      if (["henng_ui.js", "mineradio_ui.js", "app_ui.js", "tasks.js", "password_toggle.js", "admin.js", "workspace_ui.js"].includes(name)) {
        w.eval(fs.readFileSync(path.join(root, "spark_console/static", name), "utf8"));
      }
    }
    await new Promise(resolve => setTimeout(resolve, 10));
    const ids = [...d.querySelectorAll("[id]")].map(el => el.id);
    assert.equal(new Set(ids).size, ids.length, `duplicate ids: ${route}`);
    for (const input of d.querySelectorAll('input:not([type="hidden"]), select, textarea')) {
      assert(input.labels.length || input.getAttribute("aria-label") || input.getAttribute("aria-labelledby"), `unlabelled control ${route}: ${input.outerHTML}`);
    }
    assert(d.querySelector('link[href^="/static/henng.css"]'), `shared css missing ${route}`);
    const visibleCopy = d.body.cloneNode(true);
    visibleCopy.querySelectorAll("script,style").forEach(node => node.remove());
    assert(!/HENNG|henng\.cn/i.test(visibleCopy.textContent), `domain branding visible ${route}`);
    const palette = d.querySelector("[data-command-dialog]");
    if (palette) {
      const opener = d.querySelector("[data-command-open]");
      assert.equal(opener.hidden, false);
      opener.focus(); opener.click();
      assert(palette.open);
      const input = palette.querySelector("input");
      assert.equal(d.activeElement, input);
      input.value = "运维"; input.dispatchEvent(new w.Event("input"));
      assert.equal(palette.querySelectorAll("[data-command-item]:not([hidden])").length, 1);
      assert.equal(input.getAttribute("aria-activedescendant"), "workspace-command-ops");
      input.value = "missing-page"; input.dispatchEvent(new w.Event("input"));
      assert.equal(palette.querySelector("[data-command-empty]").hidden, false);
      assert.equal(input.hasAttribute("aria-activedescendant"), false);
      input.value = ""; input.dispatchEvent(new w.Event("input"));
      input.dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
      assert.equal(input.getAttribute("aria-activedescendant"), "workspace-command-2");
      palette.querySelector("[data-command-close]").click();
      assert(!palette.open);
      assert.equal(d.activeElement, opener);
    }
    if (route === "/dashboard" || route === "/tasks") {
      assert.equal(d.querySelectorAll(".hn-hour").length, 24);
      assert.equal(d.querySelector("[data-schedule]").hidden, false);
      d.querySelector('.hn-hour[data-hour="0"]').click();
      assert.equal(d.querySelector('.hn-hour[data-hour="0"]').getAttribute("aria-pressed"), "true");
      assert.equal(d.querySelector("[data-schedule-caption]").textContent, "00:00 - 01:00");
      d.querySelector('.hn-hour[data-hour="0"]').dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
      assert.equal(d.activeElement.dataset.hour, "1");
      d.querySelector("[data-schedule-reset]").click();
      assert.equal(d.querySelectorAll('.hn-hour[aria-pressed="true"]').length, 0);
    }
    if (route === "/dashboard") {
      assert(d.querySelector("#sp-recent-panel").hidden);
      d.querySelector("#sp-recent-tab").click();
      assert(d.querySelector("#sp-plan-panel").hidden);
      assert(!d.querySelector("#sp-recent-panel").hidden);
      d.querySelector("#sp-recent-tab").dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowLeft", bubbles: true}));
      assert(!d.querySelector("#sp-plan-panel").hidden);
      assert.equal(d.activeElement.id, "sp-plan-tab");
      d.querySelector('.hn-hour[data-hour="9"]').click();
      assert.equal(new URL(d.querySelector("[data-schedule-link]").href).searchParams.get("task_hour"), "9");
      assert(d.querySelector("[data-schedule-summary]").textContent.startsWith("13 个任务"));
      assert.equal(d.querySelectorAll("[data-schedule-targets] > span").length, 4);
    }
    if (route === "/tasks") {
      d.querySelector('[data-density="compact"]').click();
      assert.equal(d.querySelector(".task-overview").dataset.density, "compact");
      assert.equal(w.localStorage.getItem("spark-task-density"), "compact");
      d.querySelector('[data-density="comfortable"]').click();
      d.querySelector('.hn-hour[data-hour="0"]').click();
      assert.equal(d.querySelectorAll(".task-overview-row:not([hidden])").length, 0);
      assert.equal(d.querySelector("[data-task-no-results]").hidden, false);
      assert.equal(new URL(w.location.href).searchParams.get("task_hour"), "0");
      d.querySelector("[data-task-filter-clear]").click();
      assert.equal(new URL(w.location.href).searchParams.has("task_hour"), false);
      assert.equal(d.querySelectorAll('.hn-hour[aria-pressed="true"]').length, 0);
      d.querySelector('.hn-hour[data-hour="9"]').click();
      assert.equal(new URL(w.location.href).searchParams.get("task_hour"), "9");
      d.querySelector("[data-schedule-reset]").click();
      assert.equal(d.querySelectorAll(".task-overview-row:not([hidden])").length, 8);
      d.querySelector("[data-task-next]").click();
      assert.equal(d.querySelectorAll(".task-overview-row:not([hidden])").length, 5);
      const search = d.querySelector("#hn-task-search");
      search.value = "missing";
      search.dispatchEvent(new w.Event("input"));
      assert.equal(d.querySelector("[data-task-no-results]").hidden, false);
      d.querySelector("[data-task-filter-clear]").click();
      d.querySelector("[data-create-open]").click();
      assert(d.querySelector(".hn-drawer").open);
      d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "k", ctrlKey: true, bubbles: true}));
      assert(!palette.open, "command palette must not cover an active form");
      d.querySelector("#target-picker-trigger").click();
      assert(d.querySelector("#target-picker-dialog").open);
      const avatar = d.querySelector(".target-option-avatar img");
      assert(avatar);
      avatar.dispatchEvent(new w.Event("error"));
      assert(!d.querySelector(".target-option-avatar img"));
      d.querySelector("[data-target-select-all]").click();
      d.querySelector("[data-target-confirm]").click();
      assert.equal(d.querySelectorAll('input[name="target_name"]').length, 2);
      d.querySelector(".hn-drawer-close").click();
      assert(!d.querySelector(".hn-drawer").open);
      const edit = d.querySelector('.task-row-action[href$="/edit"]');
      edit.click();
      await new Promise(resolve => setTimeout(resolve, 25));
      assert(d.querySelector(".hn-edit-drawer")?.open, "edit drawer not opened");
      assert.equal(d.querySelectorAll("#task-account").length, 1, "create and edit ids collide");
      assert.equal(d.querySelector(".edit-task-form").getAttribute("action"), new URL(edit.href).pathname);
      d.querySelector(".hn-edit-drawer .hn-drawer-close").click();
      assert.equal(d.querySelectorAll("#task-account").length, 1, "create form not restored");
      assert.equal(d.querySelectorAll('input[name="target_name"]').length, 2, "create draft lost");
    }
    if (route.startsWith("/admin/")) {
      for (const table of d.querySelectorAll(".admin-table")) assert(table.classList.contains("hn-responsive-table"));
    }
    dom.window.close();
  }
  process.stdout.write(JSON.stringify({ok: true, pages: Object.keys(pages).length}));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
